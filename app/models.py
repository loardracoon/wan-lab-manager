"""Topology schema: permissive on purpose (extra fields flow through),
strict only where inconsistencies would break the app at runtime."""
import copy
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from . import config


class Topology(BaseModel):
    model_config = ConfigDict(extra="allow")

    settings: dict = {}
    sshGateway: dict = {}
    vyos: dict = {}
    linkProfiles: dict = {}
    isps: list
    sites: list
    links: list

    @field_validator("isps", "sites", "links")
    @classmethod
    def _non_empty_entries(cls, v):
        for item in v:
            if not isinstance(item, dict) or not item.get("id"):
                raise ValueError("every entry needs an object with an 'id'")
        return v

    @model_validator(mode="after")
    def _cross_checks(self):
        ids = [i["id"] for i in self.isps] + [s["id"] for s in self.sites]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicated ids across isps/sites")

        node_ids = set(ids)
        routers = (self.vyos or {}).get("routers", {}) or {}
        for link in self.links:
            if link.get("from") not in node_ids or link.get("to") not in node_ids:
                raise ValueError(f"link '{link.get('id')}' references unknown from/to")
            ctl = link.get("control")
            if ctl and ctl.get("router") not in routers:
                raise ValueError(
                    f"link '{link.get('id')}' control.router '{ctl.get('router')}' "
                    "is not defined in vyos.routers"
                )

        default = (self.settings or {}).get("defaultProfile")
        if default and self.linkProfiles and default not in self.linkProfiles:
            raise ValueError(f"settings.defaultProfile '{default}' is not in linkProfiles")
        return self


def validate_topology(data: dict) -> dict:
    return Topology.model_validate(data).model_dump()


def redact(data: dict) -> dict:
    """Return a deep copy with router API keys hidden."""
    out = copy.deepcopy(data)
    for router in (out.get("vyos", {}) or {}).get("routers", {}).values():
        if router.get("apiKey"):
            router["apiKey"] = config.REDACTED
    return out


def merge_secrets(incoming: dict, current: dict | None) -> dict:
    """Allow re-importing a redacted export: any apiKey equal to the
    redaction marker is replaced by the key currently stored."""
    if not current:
        return incoming
    stored = (current.get("vyos", {}) or {}).get("routers", {})
    for rid, router in (incoming.get("vyos", {}) or {}).get("routers", {}).items():
        if router.get("apiKey") == config.REDACTED:
            router["apiKey"] = stored.get(rid, {}).get("apiKey", "")
    return incoming
