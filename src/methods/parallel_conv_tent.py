"""
Copyright to Tent Authors ICLR 2021 Spotlight
"""

from argparse import ArgumentDefaultsHelpFormatter
from copy import deepcopy

import torch
import torch.nn as nn
import torch.jit

from torch.autograd import Variable


class Tent_para(nn.Module):
    """Tent adapts a model by entropy minimization during testing.
    Once tented, a model adapts itself by updating on every forward.
    """
    def __init__(self, model, optimizer, steps=1, episodic=False, adaptation_type = 'otta'):
        super().__init__()
        self.model = model
        self.optimizer = optimizer
        self.steps = steps
        assert steps > 0, "tent requires >= 1 step(s) to forward and update"
        self.episodic = episodic

        # note: if the model is never reset, like for continual adaptation,
        # then skipping the state copy would save memory
        self.model_state, self.optimizer_state = \
            copy_model_and_optimizer(self.model, self.optimizer)

        self.adaptation_type = adaptation_type


    ######################################################################################################################################## forward start

    def forward(self, x):

        ################################################################################################################ if for ttba start

        if self.adaptation_type == 'ttba':

            self.reset()

            self.model.train()

            if self.steps > 0:
                for _ in range(self.steps):
                    outputs = forward_and_adapt(x, self.model, self.optimizer)

            self.model.eval()
            with torch.no_grad():
                outputs = self.model(x)

        ################################################################################################################ if for ttba end


        ####################################################################################### if for otta, ttda start

        elif (self.adaptation_type == 'otta') or (self.adaptation_type == 'ttda'):

            self.model.train()

            if self.steps > 0:
                for _ in range(self.steps):
                    outputs = forward_and_adapt(self, x, self.model, self.optimizer)

        ######################################################################################## if for otta end

        return outputs

    ######################################################################################################################################### forward end


    def reset(self):
        if self.model_state is None or self.optimizer_state is None:
            raise Exception("cannot reset without saved model/optimizer state")
        load_model_and_optimizer(self.model, self.optimizer,
                                 self.model_state, self.optimizer_state)

    def reset_steps(self, new_steps):
        self.steps = new_steps

    @staticmethod
    def configure_model(model):
        """Configure model for use with tent."""
        # train mode, because tent optimizes the model to minimize entropy
        model.train()

        # disable grad, to (re-)enable only what tent updates
        model.requires_grad_(False)

        # configure norm for tent updates: enable grad + force batch statisics
        for nm, m in model.named_modules():
            if "parallel" in nm.split("."):
                m.requires_grad_(True)

        return model

    @staticmethod
    def collect_params(model):
        """Collect the affine scale + shift parameters from batch norms.
        Walk the model's modules and collect all batch normalization parameters.
        Return the parameters and their names.
        Note: other choices of parameterization are possible!
        """

        params = []
        names = []

        for nm, m in model.named_modules():
            if "parallel" in nm.split("."):
                for np, p in m.named_parameters():
                    if np in ['weight', 'bias']:  # weight is scale, bias is shift
                        params.append(p)
                        names.append(f"{nm}.{np}")

        return params, names

    @staticmethod
    def check_model(model):
        """Check model for compatability with tent."""
        is_training = model.training
        assert is_training, "tent needs train mode: call model.train()"
        param_grads = [p.requires_grad for p in model.parameters()]
        has_any_params = any(param_grads)
        has_all_params = all(param_grads)
        assert has_any_params, "tent needs params to update: " \
                               "check which require grad"
        assert not has_all_params, "tent should not update all params: " \
                                   "check which require grad"
        has_bn = any([isinstance(m, nn.BatchNorm2d) for m in model.modules()])
        assert has_bn, "tent needs normalization for its optimization"

@torch.jit.script
def softmax_entropy(x: torch.Tensor) -> torch.Tensor:
    """Entropy of softmax distribution from logits."""
    temprature = 1
    x = x/ temprature
    x = -(x.softmax(1) * x.log_softmax(1)).sum(1)
    return x

def mean_softmax_entropy(x:torch.Tensor)->torch.Tensor:
    temprature = 1
    x = x / temprature
    mean_probe_d=torch.mean(x.softmax(1),dim=0)
    entropy=-torch.sum(mean_probe_d*torch.log(mean_probe_d))
    return entropy


@torch.jit.script
def energy(x: torch.Tensor) -> torch.Tensor:
    """Energy calculation from logits."""
    temprature = 1
    x = -(temprature*torch.logsumexp(x / temprature, dim=1))
    if torch.rand(1) > 0.95:
        print(x.mean(0).item())
    return x


@torch.enable_grad()  # ensure grads in possible no grad context for testing
def forward_and_adapt(self, x, model, optimizer):
    """Forward and adapt model on batch of data.
    Measure entropy of the model prediction, take gradients, and update params.
    """
    # forward
    outputs = model(x)
    # adapt
    loss = softmax_entropy(outputs).mean(0)

    loss.backward()
    optimizer.step()
    optimizer.zero_grad()

    with open(f'/mnt/HDD2/HW_2/Benchmark-TTA/output/test-time-evaluation/debug/tent_{str(model)[:11]}.txt', 'a') as f:
        f.write(f'loss: {loss} \n')

    return outputs

def copy_model_and_optimizer(model, optimizer):
    """Copy the model and optimizer states for resetting after adaptation."""
    model_state = deepcopy(model.state_dict())
    optimizer_state = deepcopy(optimizer.state_dict())
    return model_state, optimizer_state


def load_model_and_optimizer(model, optimizer, model_state, optimizer_state):
    """Restore the model and optimizer states from copies."""
    model.load_state_dict(model_state, strict=True)
    optimizer.load_state_dict(optimizer_state)
