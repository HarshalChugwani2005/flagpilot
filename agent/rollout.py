"""Rolling out a winner: the rollout plan, its description, and reading Flagsmith's replies.

The rollout uses official Flagsmith MCP tools only:

1. ``list_project_environments``: find the environment by its API key (and refuse v2
   feature versioning, which needs the versioning tools instead).
2. ``list_project_features`` (``environment=<id>``): the flag's id and feature state id.
3. ``list_feature_multivariate_options``: option ids, matched on the stored value.
4. ``update_environment_feature_state``: winner 100%, every other option 0%. When the
   control wins, every option is set to 0% so all identities get the control value.
"""

import json
from typing import Any

from pydantic import BaseModel

from agent.changes import match_options, option_values
from agent.errors import FlagPilotError
from agent.models import ExperimentPlan, ExperimentResult, RolloutPlan

RESPONSE_PREVIEW_CHARS = 500


class RolloutError(FlagPilotError):
    """The rollout cannot go ahead, or Flagsmith's reply was not understood."""


class EnvironmentRef(BaseModel):
    """The Flagsmith environment being rolled out."""

    id: int
    name: str
    uses_v2_versioning: bool


class FeatureStateRef(BaseModel):
    """The flag and its feature state in that environment."""

    feature_id: int
    feature_state_id: int


def build_rollout(plan: ExperimentPlan, result: ExperimentResult) -> RolloutPlan:
    """Rollout plan for a significant winner; every other variant becomes stale."""
    if result.verdict != "winner" or result.winner is None:
        raise RolloutError("There is no significant winner to roll out yet.")
    return RolloutPlan(
        flag_name=plan.flag_name,
        control=plan.variants[0].name,
        winner=result.winner,
        stale_variants=[v.name for v in plan.variants if v.name != result.winner],
        environment=plan.environment,
        values=option_values(plan),
    )


def describe_rollout(rollout: RolloutPlan) -> list[str]:
    """Human-readable lines for the second-approval dialog."""
    return [
        f'Send 100% of identities on "{rollout.flag_name}" to "{rollout.winner}"',
        *(f'Set "{name}" to 0% and record it as stale' for name in rollout.stale_variants),
    ]


def _preview(response: Any) -> str:
    """Short JSON preview of a response for error details."""
    return json.dumps(response, default=str)[:RESPONSE_PREVIEW_CHARS]


def _items(response: Any, what: str) -> list[dict[str, Any]]:
    """Return the list inside a (possibly paginated) Flagsmith response."""
    items = response.get("results") if isinstance(response, dict) else response
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RolloutError(
            f"Flagsmith returned an unexpected {what} response.", detail=_preview(response)
        )
    return items


def find_environment(response: Any, api_key: str) -> EnvironmentRef:
    """Return the environment whose ``api_key`` matches FLAGSMITH_ENVIRONMENT_KEY."""
    for item in _items(response, "environments"):
        if item.get("api_key") == api_key:
            try:
                return EnvironmentRef(
                    id=item["id"],
                    name=str(item.get("name", "")),
                    uses_v2_versioning=bool(item.get("use_v2_feature_versioning", False)),
                )
            except (KeyError, ValueError) as error:
                raise RolloutError(
                    "Flagsmith's environment details were incomplete.", detail=_preview(item)
                ) from error
    raise RolloutError("The environment in FLAGSMITH_ENVIRONMENT_KEY is not in this project.")


def find_feature_state(response: Any, flag_name: str) -> FeatureStateRef:
    """Return the flag's id and its feature state id in the environment."""
    for item in _items(response, "features"):
        if item.get("name") != flag_name:
            continue
        state = item.get("environment_feature_state")
        try:
            return FeatureStateRef(feature_id=item["id"], feature_state_id=state["id"])
        except (KeyError, TypeError, ValueError) as error:
            raise RolloutError(
                f'Flagsmith did not return the feature state for "{flag_name}".',
                detail=_preview(item),
            ) from error
    raise RolloutError(f'Flag "{flag_name}" was not found in Flagsmith.')


def option_ids(response: Any, values: dict[str, str]) -> dict[str, int]:
    """Multivariate option id per variant name, matched on the stored option value."""
    return match_options(_items(response, "multivariate options"), values)


def rollout_allocations(rollout: RolloutPlan, ids: dict[str, int]) -> list[dict[str, Any]]:
    """``multivariate_feature_state_values`` giving the winner 100% (control: all 0%)."""
    expected = {name for name in [rollout.winner, *rollout.stale_variants]} - {rollout.control}
    missing = expected - set(ids)
    if missing:
        raise RolloutError(
            f"Flagsmith has no option for {', '.join(sorted(missing))} on {rollout.flag_name}."
        )
    return [
        {
            "multivariate_feature_option": option_id,
            "percentage_allocation": 100 if key == rollout.winner else 0,
        }
        for key, option_id in sorted(ids.items())
    ]
