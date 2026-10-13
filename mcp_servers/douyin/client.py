"""巨量引擎开放平台 HTTP 客户端（抖音 / 巨量千川）。

- 鉴权：OAuth2。日常调用使用 access_token；过期时可用 app_id + app_secret + refresh_token 续期。
- 调用：统一注入 access_token / advertiser_id；GET 走 query，POST 走 query(token) + JSON body。
- 只负责「发请求 + 解析」，不含业务语义，供 MCP 工具与后端投递 Agent 共用。
"""
from __future__ import annotations

import httpx

from .config import is_real, qianchuan_config


class QianchuanAPIError(RuntimeError):
    """千川 API 相关错误（未配置 / 鉴权失败 / 接口报错 / 网络异常）。"""


class QianchuanClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or qianchuan_config()

    # ------------------------------------------------------------------ 鉴权
    def refresh_access_token(self) -> dict:
        """用 refresh_token 续期，返回新的 access_token / refresh_token。"""
        cfg = self.cfg
        if not (is_real(cfg["app_id"]) and is_real(cfg["app_secret"]) and is_real(cfg["refresh_token"])):
            raise QianchuanAPIError(
                "刷新 token 需要 QIANCHUAN_APP_ID / QIANCHUAN_APP_SECRET / QIANCHUAN_REFRESH_TOKEN（见 .env.example）"
            )
        url = f"{cfg['base_url']}/open_api/oauth2/refresh_token/"
        payload = {
            "app_id": cfg["app_id"],
            "secret": cfg["app_secret"],
            "grant_type": "refresh_token",
            "refresh_token": cfg["refresh_token"],
        }
        with httpx.Client(timeout=cfg["timeout"]) as client:
            body = self._parse(client.post(url, json=payload))
        data = body.get("data") or {}
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in"),
        }

    # ------------------------------------------------------------------ 调用
    def call(self, path: str, params: dict | None = None, method: str = "GET", advertiser_id=None) -> dict:
        """调用任一开放平台接口，返回解析后的响应体。

        advertiser_id：默认取 .env 中的 QIANCHUAN_ADVERTISER_ID；显式传 "" 表示不带该参数。
        """
        cfg = self.cfg
        if not is_real(cfg["access_token"]):
            raise QianchuanAPIError(
                "未配置 QIANCHUAN_ACCESS_TOKEN，请在项目根目录 .env 中填写"
                "（配置说明见 .env.example 的「抖音 · 巨量千川」段）"
            )

        params = dict(params or {})
        token_params = {"access_token": cfg["access_token"]}
        adv = cfg["advertiser_id"] if advertiser_id is None else advertiser_id
        if adv:
            token_params["advertiser_id"] = adv

        url = f"{cfg['base_url']}{path}"
        with httpx.Client(timeout=cfg["timeout"]) as client:
            if method.upper() == "POST":
                resp = client.post(url, params=token_params, json=params)
            else:
                resp = client.get(url, params={**token_params, **params})
        return self._parse(resp)

    # ------------------------------------------------------------------ 解析
    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            raise QianchuanAPIError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise QianchuanAPIError(f"响应不是合法 JSON：{resp.text[:200]}") from exc
        code = body.get("code", 0)
        if code not in (0, None):
            raise QianchuanAPIError(
                f"接口返回错误 code={code}：{body.get('message') or body.get('msg') or body}"
            )
        return body