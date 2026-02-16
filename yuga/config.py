"""Configuration loader with env var overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PolymarketConfig:
    clob_base_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    funder: str = ""


@dataclass
class StrategyConfig:
    max_markets: int = 50
    quote_spread_bps: int = 20
    scan_interval_ms: int = 500
    order_size_usdc: float = 10.0
    max_order_size_usdc: float = 100.0
    min_liquidity_usdc: float = 50.0
    price_staleness_ms: int = 2000
    quote_refresh_ms: int = 2000
    quote_ttl_ms: int = 15000
    reprice_threshold_bps: int = 5
    require_fee_enabled: bool = False
    orderbook_dwell_s: float = 8.0
    orderbook_auto_rotate: bool = False


@dataclass
class RiskConfig:
    max_total_exposure_usdc: float = 1000.0
    max_per_market_exposure_usdc: float = 200.0
    max_daily_loss_usdc: float = 50.0
    max_consecutive_losses: int = 5
    circuit_breaker_cooldown_s: int = 300
    max_open_orders: int = 20
    position_limit_per_outcome: float = 500.0


@dataclass
class ExecutionConfig:
    order_timeout_ms: int = 5000
    max_retries: int = 2
    fill_poll_interval_ms: int = 100
    cancel_stale_after_ms: int = 3000


@dataclass
class DatabaseConfig:
    path: str = "yuga.db"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "yuga.log"


@dataclass
class AppConfig:
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dict(dc: object, d: dict) -> None:
    for k, v in d.items():
        if hasattr(dc, k):
            setattr(dc, k, v)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    cfg = AppConfig()
    p = Path(path)
    if p.exists():
        with open(p) as f:
            raw = yaml.safe_load(f) or {}
        for section_name, section_cfg in [
            ("polymarket", cfg.polymarket),
            ("strategy", cfg.strategy),
            ("risk", cfg.risk),
            ("execution", cfg.execution),
            ("database", cfg.database),
            ("logging", cfg.logging),
        ]:
            if section_name in raw:
                _merge_dict(section_cfg, raw[section_name])

    # Env var overrides for secrets
    cfg.polymarket.api_key = os.getenv("POLYMARKET_API_KEY", cfg.polymarket.api_key)
    cfg.polymarket.api_secret = os.getenv("POLYMARKET_API_SECRET", cfg.polymarket.api_secret)
    cfg.polymarket.api_passphrase = os.getenv(
        "POLYMARKET_API_PASSPHRASE", cfg.polymarket.api_passphrase
    )
    cfg.polymarket.funder = os.getenv("POLYMARKET_FUNDER", cfg.polymarket.funder)

    return cfg
