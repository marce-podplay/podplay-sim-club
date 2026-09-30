"""Verify stored Preview Club identities through Firebase and /users/current.

The registry password stays in the 0600 file. Callers print the actor id,
product user id, and roles, never the email or password.
"""

from typing import Any, Dict, Iterable, List

from .identity_registry import ActorIdentity
from .firebase_auth import AuthSession
from .preview_readonly import PreviewReadError


def verify_player_session(
    actor_id: str, identity: ActorIdentity, session: AuthSession, profile: Any
) -> Dict[str, Any]:
    """Confirm the Firebase session is the registry user, without echoing secrets."""
    if identity.actor_id != actor_id:
        raise PreviewReadError(f"player registry entry does not match {actor_id}")
    if session.email.lower() != identity.email.lower():
        raise PreviewReadError(f"Firebase session does not match the registry user: {actor_id}")
    if not isinstance(profile, dict):
        raise PreviewReadError(f"current user response has an unexpected shape: {actor_id}")
    if str(profile.get("email", "")).lower() != identity.email.lower():
        raise PreviewReadError(f"current user does not match the registry user: {actor_id}")
    user_id = profile.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise PreviewReadError(f"current user id is missing: {actor_id}")
    roles = profile.get("roles")
    return {
        "actorId": actor_id,
        "authSource": session.source,
        "userId": user_id,
        "roles": [str(role) for role in roles] if isinstance(roles, list) else [],
        "emailMatched": True,
    }


def session_check_record(summaries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Make a durable, secret-free record of a stateless session check.

    Firebase tokens are intentionally not retained by this record.  This proves
    that an identity can sign in and read its own product profile; it does not
    claim to create or log out a browser-profile session.
    """
    actors: List[Dict[str, Any]] = []
    for summary in summaries:
        actors.append(
            {
                "actorId": summary["actorId"],
                "roles": list(summary.get("roles") or []),
                "verified": bool(summary.get("emailMatched")),
            }
        )
    return {
        "schemaVersion": 1,
        "mode": "stateless_preview_session_check",
        "browserSession": "not_created_or_modified",
        "actors": actors,
        "remoteWrites": 0,
    }
