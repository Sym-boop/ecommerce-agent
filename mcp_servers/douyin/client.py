"""巨量引擎开放平台 HTTP 客户端（抖音 / 巨量千川）。

- 鉴权：OAuth2。首次用 `auth_code` 换 token；日常用 `access_token`；
  过期用 `app_id + app_secret + refresh_token` 续期（续期会下发新的 refresh_token）。
- 调用：统一注入 access_token / advertiser_id；GET 走 query，POST 走 query(token) + JSON body。
- 只负责「发请求 + 解析」，不含业务语义，供 MCP 工具与后端投递 Agent 共用。

**token 生命周期**（这一块原来是有缺口的，见各方法注释）：
    auth_code ──exchange_auth_code()──> access_token + refresh_token（落 token_store）
    access_token 快过期 ──_maybe_refresh()──> 新 token（落 token_store，且本实例 cfg 立即刷新）
"""
from __future__ import annotations

import logging

import httpx

from .config import is_real, qianchuan_config

logger = logging.getLogger(__name__)


class QianchuanAPIError(RuntimeError):
    """千川 API 相关错误（未配置 / 鉴权失败 / 接口报错 / 网络异常）。"""


class QianchuanClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or qianchuan_config()

    # ------------------------------------------------------------------ 鉴权
    def exchange_auth_code(self, auth_code: str) -> dict:
        """OAuth 授权码换 token（**首次拿到 token 的唯一入口**）。

        原来没有这个方法：只能手工把 token 粘进 `.env`，等于把授权流程跳过了。
        实际链路是：前端跳授权页 → 用户在千川后台同意 → 回调带回 auth_code →
        用 auth_code 换 access_token + refresh_token。
        """
        auth_code = (auth_code or "").strip()
        if not auth_code:
            raise QianchuanAPIError("缺少 auth_code（OAuth 回调参数）")
        cfg = self.cfg
        if not (is_real(cfg["app_id"]) and is_real(cfg["app_secret"])):
            raise QianchuanAPIError(
                "换 token 需要 QIANCHUAN_APP_ID / QIANCHUAN_APP_SECRET（开放平台应用审核通过后获得）"
            )
        url = f"{cfg['base_url']}{cfg['oauth_token_path']}"
        payload = {
            "app_id": cfg["app_id"],
            "secret": cfg["app_secret"],
            "grant_type": "auth_code",
            "auth_code": auth_code,
        }
        with httpx.Client(timeout=cfg["timeout"]) as client:
            body = self._parse(client.post(url, json=payload))
        data = body.get("data") or {}
        doc = self._persist(data, source="oauth")
        return doc

    def refresh_access_token(self) -> dict:
        """用 refresh_token 续期，并**立即持久化**。

        原实现的缺口：新 token 只返回给调用方，没有任何地方保存，
        下次仍读 `.env` 的旧值 → 续期等于没做。
        """
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
        doc = self._persist(data, source="refresh")
        return doc

    def _persist(self, data: dict, source: str) -> dict:
        """把授权结果落盘，并让当前实例的 cfg 立刻用上新 token。"""
        from . import token_store

        doc = token_store.save(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_in=data.get("expires_in"),
            advertiser_id=data.get("advertiser_id") or self.cfg.get("advertiser_id", ""),
            source=source,
        )
        # 关键：cfg 是启动时的快照，不重读就会继续用旧 token
        self.cfg = qianchuan_config()
        return {
            "access_token": doc.get("access_token", ""),
            "refresh_token": doc.get("refresh_token", ""),
            "expires_in": data.get("expires_in"),
            "source": source,
        }

    def _maybe_refresh(self) -> None:
        """调用前检查剩余有效期，快过期就先续。

        失败只记日志、不抛出：让真实的业务请求去暴露错误码，
        比在这里抛一个「续期失败」更接近平台实际返回（有时只是网络抖动）。
        """
        try:
            from . import token_store

            if not token_store.needs_refresh():
                return
            cfg = qianchuan_config()
            if not (is_real(cfg["app_id"]) and is_real(cfg["app_secret"]) and is_real(cfg["refresh_token"])):
                return
            self.cfg = cfg
            self.refresh_access_token()
            logger.info("千川 access_token 已自动续期")
        except Exception as exc:  # noqa: BLE001
            logger.warning("千川 token 自动续期失败：%s", exc)

    # ------------------------------------------------------------------ 调用
    def call(self, path: str, params: dict | None = None, method: str = "GET", advertiser_id=None) -> dict:
        """调用任一开放平台接口，返回解析后的响应体。

        advertiser_id：默认取 .env 中的 QIANCHUAN_ADVERTISER_ID；显式传 "" 表示不带该参数。
        """
        self._maybe_refresh()
        cfg = self.cfg
        if not is_real(cfg["access_token"]):
            raise QianchuanAPIError(
                "千川未授权：请先在投放页完成 OAuth 授权，或配置 QIANCHUAN_ACCESS_TOKEN"
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
