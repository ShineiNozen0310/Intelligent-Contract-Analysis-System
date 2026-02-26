from typing import Any
from detection_models.yolo_stamp.constants import *
from detection_models.yolo_stamp.utils import *
from detection_models.yolo_stamp.model import SymReLU, YOLOStamp
import albumentations as A
from albumentations.pytorch.transforms import ToTensorV2
import torch
from huggingface_hub import hf_hub_download
import numpy as np


def _safe_load_yolo_stamp(path: str):
    allowlist = [
        YOLOStamp,
        SymReLU,
        torch.nn.Conv2d,
        torch.nn.BatchNorm2d,
        torch.nn.MaxPool2d,
    ]
    safe_globals = getattr(torch.serialization, "safe_globals", None)
    if safe_globals is not None:
        with safe_globals(allowlist):
            return torch.load(path, map_location="cpu", weights_only=True)

    add_safe_globals = getattr(torch.serialization, "add_safe_globals", None)
    if add_safe_globals is not None:
        add_safe_globals(allowlist)
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        return torch.load(path, map_location="cpu")


class YoloStampPipeline:
    def __init__(self):
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.model = None
        self.transform = A.Compose([
            A.Normalize(),
            ToTensorV2(p=1.0),
        ])
    
    @classmethod
    def from_pretrained(cls, model_path_hf: str = None, filename_hf: str = "weights.pt", local_model_path: str = None):
        yolo = cls()
        if model_path_hf is not None and filename_hf is not None:
            yolo.model = _safe_load_yolo_stamp(hf_hub_download(model_path_hf, filename=filename_hf))
            yolo.model.to(yolo.device)
            yolo.model.eval()
        elif local_model_path is not None:
            yolo.model = _safe_load_yolo_stamp(local_model_path)
            yolo.model.to(yolo.device)
            yolo.model.eval()
        return yolo
    
    def __call__(self, image) -> torch.Tensor:
        shape = torch.tensor(image.size)
        coef =  torch.hstack((shape, shape)) / 448
        image = image.convert("RGB").resize((448, 448))
        image_np = np.array(image)
        image_tensor = self.transform(image=image_np)["image"]
        output = self.model(image_tensor.unsqueeze(0).to(self.device))
        if isinstance(output, (list, tuple)):
            if not output:
                return torch.empty((0, 4))
            output_tensor = output[0]
        else:
            output_tensor = output
        if not torch.is_tensor(output_tensor):
            return torch.empty((0, 4))
        if output_tensor.ndim == 5 and output_tensor.shape[0] == 1:
            output_tensor = output_tensor[0]
        if output_tensor.ndim != 4:
            return torch.empty((0, 4))
        boxes = output_tensor_to_boxes(output_tensor.detach().cpu())
        boxes = nonmax_suppression(boxes=boxes)
        if boxes is None or len(boxes) == 0:
            return torch.empty((0, 4))
        t = torch.tensor(boxes)
        if t.ndim == 1:
            if t.numel() < 4:
                return torch.empty((0, 4))
            t = t.view(1, -1)
        t = t[:, :4]
        boxes_xy = xywh2xyxy(t)
        boxes_xy = boxes_xy * coef
        return boxes_xy
