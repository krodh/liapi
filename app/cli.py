"""Command-line interface: capture a LinkedIn session and check it."""

from __future__ import annotations

import typer

from app import auth as auth_file
from app.auth import DURABILITY, RECOMMENDED, REQUIRED, Auth, AuthError
from app.config import get_settings
from app.session import LinkedInSession

cli = typer.Typer(
    help="Capture and inspect a LinkedIn session.",
    no_args_is_help=True,
)


def _describe(auth: Auth) -> None:
    """Print what the credential contains and how durable it looks."""
    if auth.account.public_id:
        typer.echo(f"account:  {auth.account.public_id}")
    typer.echo(f"captured: {auth.age_hours:.1f}h ago")
    typer.echo(f"cookies:  {len(auth.cookies)}")
    if agent := auth.client.user_agent:
        typer.echo(f"client:   {auth.client.impersonate}  ({agent[:52]}…)")

    for label, names in (
        ("required", REQUIRED),
        ("durability", DURABILITY),
        ("recommended", RECOMMENDED),
    ):
        if missing := auth.missing(names):
            colour = "red" if label == "required" else "yellow"
            typer.secho(f"missing {label}: {', '.join(missing)}", fg=colour)

    if not auth.durable and auth.usable:
        # li_rm only exists if "Keep me signed in" was ticked, so the fix is to
        # log in again with that box checked, not to re-capture the same way.
        typer.secho(
            '  li_rm is missing: log in again with "Keep me signed in" ticked, '
            "then re-capture.",
            fg="yellow",
        )


@cli.command()
def capture(
    headless: bool = typer.Option(
        False, "--headless", help="Run the browser without a window."
    ),
    timeout: int = typer.Option(300, help="Seconds to wait for the login."),
) -> None:
    """Capture a session by logging in through a real browser.

    A setup-time step only. It harvests the cookies *and* the client context
    (user agent, locale, x-li-track) that a cookie paste cannot provide, and
    writes them to auth.json. The API itself never launches a browser.

    To write auth.json by hand instead, see docs/auth.md.
    """
    from app.capture import CaptureError, capture as run_capture

    settings = get_settings()
    try:
        auth = run_capture(headless=headless, timeout_s=timeout)
    except CaptureError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc

    _describe(auth)
    if not auth.usable:
        raise typer.Exit(1)

    typer.echo("verifying with LinkedIn…")
    if not LinkedInSession(auth, settings).is_alive():
        typer.secho("LinkedIn rejected the captured session.", fg="red", err=True)
        raise typer.Exit(1)

    auth_file.save(settings.auth_file, auth)
    typer.secho(f"saved {settings.auth_file}", fg="green")


@cli.command()
def status() -> None:
    """Report whether the saved session is still alive."""
    settings = get_settings()
    try:
        auth = auth_file.load(settings.auth_file)
    except AuthError as exc:
        typer.secho(str(exc), fg="yellow", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"source:   {settings.auth_file}")
    _describe(auth)

    if not auth.usable:
        typer.secho("session:  unusable", fg="red")
        raise typer.Exit(1)
    if not LinkedInSession(auth, settings).is_alive():
        typer.secho("session:  expired", fg="red")
        raise typer.Exit(1)
    typer.secho("session:  alive", fg="green")


if __name__ == "__main__":
    cli()
