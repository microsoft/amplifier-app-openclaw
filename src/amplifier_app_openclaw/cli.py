"""Amplifier-OpenClaw CLI entry point."""

import click

from amplifier_app_openclaw import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="amplifier-openclaw")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Amplifier × OpenClaw integration CLI."""
    if ctx.invoked_subcommand is None and not ctx.protected_params:
        click.echo(ctx.get_help())
