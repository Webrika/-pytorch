"""Test the support on onnxscript in PyTorch-ONNX converter."""
import onnxscript
import torch
from torch.testing._internal import common_utils
from torch.onnx._internal import jit_utils
import onnx_test_common
from onnxscript.onnx_types import FLOAT
from typing import List


class TestONNXScriptRuntime(onnx_test_common._TestONNXRuntime):

    # opset version is
    # 1. local function is supported after opset 15
    # 2. onnx-script requires users to determine opset in local function
    opset_version = 15

    def test_selu_from_onnxscript_example(self):

        x = torch.randn(1, 2, 3, 4, requires_grad=True)
        model = torch.nn.SELU()

        # custom domain is needed for custom Op
        # domain name should be aligned to the one
        # in symbolic_fn
        # TODO: make an official domain for onnxscript usage
        custom_opset = onnxscript.values.Opset("onnxscript", 1)

        from onnxscript.onnx_opset import opset15 as op
        @onnxscript.script(custom_opset)
        def Selu(X, alpha: float, gamma: float):
            # TODO: onnx/ort doesn't support default values for now
            # move this when they do
            alpha = 1.67326  # auto wrapped as Constants
            gamma = 1.0507
            alphaX = op.CastLike(alpha, X)
            gammaX = op.CastLike(gamma, X)
            neg = gammaX * (alphaX * op.Exp(X) - alphaX)
            pos = gammaX * X
            zero = op.CastLike(0, X)
            return op.Where(X <= zero, neg, pos)

        def custom_selu(g: jit_utils.GraphContext, X):
            return g.op("onnxscript::Selu", X).setType(X.type())

        torch.onnx.register_custom_op_symbolic(
            symbolic_name="aten::selu",
            symbolic_fn=custom_selu,
            opset_version=self.opset_version,
            onnxscript_fn=Selu,  # newly support
        )
        self.run_test(model, x)

    def test_layer_norm(self):

        x = torch.randn(2, 3)
        y = torch.randn(2, 3)
        z = torch.randn(2, 3)

        class N(torch.nn.Module):
            def __init__(self, prob):
                super().__init__()
                self.dropout = torch.nn.Dropout(prob)

            def forward(self, x):
                return self.dropout(x)

        class M(torch.nn.Module):
            def __init__(self, num_layers):
                super().__init__()
                self.num_layers = num_layers
                self.lns = torch.nn.ModuleList(
                    [torch.nn.LayerNorm(3, eps=i) for i in range(num_layers)]
                )
                self.celu1 = torch.nn.CELU(1.0)
                self.celu2 = torch.nn.CELU(2.0)
                self.dropout = N(0.5)

            def forward(self, x, y, z):
                res1 = self.celu1(x)
                res2 = self.celu2(y)
                for ln in self.lns:
                    z = ln(z)
                return res1 + res2, self.dropout(z)

        model = M(3)

        from onnxscript.onnx_opset import opset15 as op
        opset_version = 15
        custom_opset = onnxscript.values.Opset(domain="onnxscript", version=1)

        @onnxscript.script(custom_opset)
        def layer_norm(X, axes: List[int], weight: FLOAT[...], bias: FLOAT[...], eps: float):
            mean = op.ReduceMean(X, axes=axes)
            D = X - mean  # op.Sub(X, mean)
            DD = D * D  # op.Mul(D, D)
            var = op.ReduceMean(DD, axes=axes)
            vareps = var + eps  # op.Add(var, eps)
            stddev = op.Sqrt(vareps)
            invstddev = op.Reciprocal(stddev)
            normalized = D * invstddev  # op.Mul(D, invstddev)
            normalizedw = op.CastLike(normalized, weight)  # Type issue if missing this Op
            normalizedscaled = normalizedw * weight  # op.Mul(normalized, weight)
            return normalizedscaled + bias

        @torch.onnx.symbolic_helper.parse_args("v", "is", "v", "v", "f", "none")
        def custom_layer_norm(g, input, normalized_shape, weight, bias, eps, cudnn_enable):
            # TODO: move the comprehension into local function once it's supported by onnxscript
            axes = [-i for i in range(len(normalized_shape), 0, -1)]
            return g.op("onnxscript::layer_norm", input, weight, bias, axes_i=axes, eps_f=eps).setType(input.type())

        torch.onnx.register_custom_op_symbolic(
            symbolic_name="aten::layer_norm",
            symbolic_fn=custom_layer_norm,
            opset_version=self.opset_version,
            onnxscript_fn=layer_norm,  # newly support
        )

        self.run_test(model, (x, y, z))

if __name__ == "__main__":
    common_utils.run_tests()