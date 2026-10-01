"""
ddddocr 验证码服务封装
文档: https://github.com/xzxxn777/ddddocr
"""

import requests

DEFAULT_SERVER = "http://nas.zhuifeng1.top:7777"


class DdddOcr:
    def __init__(self, server_url: str = DEFAULT_SERVER):
        self.base = server_url.rstrip("/")
        self.session = requests.Session()

    def _post(self, path: str, **kwargs) -> dict:
        resp = self.session.post(self.base + path, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def slide_comparison(self, bg_image: bytes, slide_image: bytes) -> dict:
        """
        滑块对比：计算滑块需要移动的距离
        bg_image:    背景图（带缺口）
        slide_image: 滑块图
        返回: {"target": [x, y, w, h]} 或 {"x": offset}
        """
        return self._post("/slideComparison", files={
            "bg": ("bg.png", bg_image, "image/png"),
            "slide": ("slide.png", slide_image, "image/png"),
        })

    def capcode(self, image: bytes) -> dict:
        """滑块验证（一体化）"""
        return self._post("/capcode", files={"image": ("img.png", image, "image/png")})

    def ocr(self, image: bytes) -> str:
        """OCR 文字识别，返回识别结果字符串"""
        result = self._post("/classification", files={"image": ("img.png", image, "image/png")})
        return result.get("result", "")

    def calculate(self, image: bytes) -> str:
        """数字计算验证码"""
        result = self._post("/calculate", files={"image": ("img.png", image, "image/png")})
        return result.get("result", "")

    def detection(self, image: bytes) -> list:
        """位置识别，返回目标坐标列表"""
        result = self._post("/detection", files={"image": ("img.png", image, "image/png")})
        return result.get("result", [])

    def select(self, image: bytes, target: bytes) -> list:
        """图片点选，返回点击坐标列表"""
        result = self._post("/select", files={
            "image": ("img.png", image, "image/png"),
            "target": ("target.png", target, "image/png"),
        })
        return result.get("result", [])

    def crop(self, image: bytes) -> list:
        """图片分割"""
        result = self._post("/crop", files={"image": ("img.png", image, "image/png")})
        return result.get("result", [])
