"""从本地官方 DINOv2 源码与权重导出 patch-token ONNX，不自动下载。"""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="Meta DINOv2 源码目录")
    parser.add_argument("--weights", required=True, help="官方 dinov2_vits14_pretrain.pth")
    parser.add_argument("--out", required=True, help="输出 ONNX 路径")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.repo).resolve()))
    import torch
    from dinov2.models.vision_transformer import vit_small

    model = vit_small(patch_size=14, img_size=518, init_values=1.0, block_chunks=0).eval()
    state = torch.load(args.weights, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)

    class PatchTokens(torch.nn.Module):
        def __init__(self, backbone):
            super().__init__()
            self.backbone = backbone

        def forward(self, x):
            return self.backbone.forward_features(x)["x_norm_patchtokens"]

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(PatchTokens(model).eval(), torch.zeros(1, 3, 224, 224), str(output),
                      input_names=["image"], output_names=["patch_tokens"],
                      opset_version=17, dynamo=False)
    print(output.resolve())


if __name__ == "__main__":
    main()
