"""`wowfish update`: install the newest release over this executable."""

from __future__ import annotations

import typer

from wowfish.commands._common import fail


def update_command(
    check: bool = typer.Option(
        False, "--check", help="Only say whether a newer version exists (exit 0 either way)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before installing."),
) -> None:
    """Check GitHub for a newer release and install it in place of this executable."""
    from wowfish import __version__
    from wowfish import update as up

    exe = up.running_executable()
    if exe is not None:
        up.remove_leftovers(exe, tries=1)
    try:
        release = up.fetch_latest()
    except up.UpdateError as e:
        fail(f"could not check for updates: {e}")
    if not up.is_newer(release.version, __version__):
        typer.echo(f"wowfish {__version__} is up to date (latest release: {release.version})")
        return
    typer.echo(f"wowfish {release.version} is available (you have {__version__})")
    typer.echo(f"  {release.page_url}")
    if check:
        return
    if exe is None:
        typer.echo("running from source: update with `git pull` then `uv sync`")
        return
    if up.update_asset(release, exe) is None:
        fail(f"release {release.version} has no file for this computer; download it from the page")
    if not yes and not typer.confirm(f"Replace {exe} with {release.version}?"):
        raise typer.Exit(code=1)

    shown = [-1]

    def progress(done: int, total: int) -> None:
        if total:
            pct = 100 * done // total
            if pct // 10 != shown[0]:
                shown[0] = pct // 10
                typer.echo(f"  downloading… {pct}%")

    try:
        new = up.download_update(release, exe, progress)
        up.install_update(new, exe)
    except up.UpdateError as e:
        fail(f"{e}; download it yourself from {release.page_url}")
    typer.echo(f"updated {exe.name} to {release.version}; run it again to use the new version")


def register(app: typer.Typer) -> None:
    app.command("update")(update_command)
