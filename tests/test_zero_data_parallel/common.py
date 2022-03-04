from functools import partial

import torch
import torch.distributed as dist
import torch.nn as nn
from colossalai.logging import get_dist_logger
from colossalai.utils import checkpoint

LOGGER = get_dist_logger()

CONFIG = dict(fp16=dict(mode=None,),
              zero=dict(level=3,
                        verbose=False,
                        offload_optimizer_config=dict(device='cpu', pin_memory=True, buffer_count=5, fast_init=False),
                        offload_param_config=dict(device='cpu',
                                                  pin_memory=True,
                                                  buffer_count=5,
                                                  buffer_size=1e8,
                                                  max_in_cpu=1e9)),
              parallel=dict(pipeline=dict(size=1), tensor=dict(size=1, mode=None)))


def checkpoint_wrapper(module, enable=True):
    if enable:
        module.forward = partial(checkpoint, module.forward)
    return module


class Net(nn.Module):

    def __init__(self, checkpoint=False) -> None:
        super().__init__()
        self.fc1 = nn.Linear(5, 5)
        self.fc2 = nn.Linear(5, 5)
        self.fc3 = nn.Linear(5, 1)
        if checkpoint:
            self.fc1 = checkpoint_wrapper(self.fc1)
        self.layers = [self.fc1, self.fc2, self.fc1, self.fc2, self.fc3]

    def forward(self, x):
        for layer in self.layers:
            x = layer(x)
        return x


def allclose(tensor_a: torch.Tensor, tensor_b: torch.Tensor, loose=False) -> bool:
    if loose:
        return torch.allclose(tensor_a, tensor_b, atol=1e-3, rtol=1e-3)
    return torch.allclose(tensor_a, tensor_b)


def check_grads(model, zero_model, loose=False):
    for p, zero_p in zip(model.parameters(), zero_model.parameters()):
        zero_grad = zero_p.grad.clone().to(p.device)
        assert p.grad.dtype == zero_grad.dtype
        assert allclose(p.grad, zero_grad, loose=loose)
        LOGGER.info(torch.sum(p.grad - zero_grad))


def check_params(model, zero_model, loose=False):
    for p, zero_p in zip(model.parameters(), zero_model.parameters()):
        zero_p = zero_p.clone().to(p.device)
        assert p.dtype == zero_p.dtype
        assert allclose(p, zero_p, loose=loose)


def check_grads_padding(model, zero_model, loose=False):
    rank = dist.get_rank()
    for p, zero_p in zip(model.parameters(), zero_model.parameters()):
        zero_grad = zero_p.grad.clone().to(p.device)
        chunks = torch.flatten(p.grad).chunk(dist.get_world_size())
        if rank >= len(chunks):
            continue
        grad = chunks[rank]
        if zero_grad.size(0) > grad.size(0):
            zero_grad = zero_grad[:grad.size(0)]
        assert grad.dtype == zero_grad.dtype
        assert allclose(grad, zero_grad, loose=loose)


def check_params_padding(model, zero_model, loose=False):
    rank = dist.get_rank()
    for p, zero_p in zip(model.parameters(), zero_model.parameters()):
        zero_p = zero_p.clone().to(p.device)
        chunks = torch.flatten(p).chunk(dist.get_world_size())
        if rank >= len(chunks):
            continue
        p = chunks[rank]
        if zero_p.size(0) > p.size(0):
            zero_p = zero_p[:p.size(0)]
        assert p.dtype == zero_p.dtype
        assert allclose(p, zero_p, loose=loose)


def check_sharded_params_padding(model, zero_model, loose=False):
    rank = dist.get_rank()
    for p, zero_p in zip(model.parameters(), zero_model.parameters()):
        zero_p = zero_p.ca_attr.payload(p.device)
        chunks = torch.flatten(p).chunk(dist.get_world_size())
        if rank >= len(chunks):
            continue
        p = chunks[rank]
        if zero_p.size(0) > p.size(0):
            zero_p = zero_p[:p.size(0)]
        assert p.dtype == zero_p.dtype
        assert allclose(p, zero_p, loose=loose)