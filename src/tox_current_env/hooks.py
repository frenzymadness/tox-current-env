import os
import shutil
import subprocess
import sys
import tox
import warnings
import argparse

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata


@tox.hookimpl
def tox_addoption(parser):
    parser.add_argument(
        "--current-env",
        action="store_true",
        dest="current_env",
        default=False,
        help="Run tests in current environment, not creating any virtual environment",
    )
    parser.add_argument(
        "--print-deps-only",
        action="store_true",
        dest="print_deps_only",
        default=False,
        help="Deprecated, equivalent to `--print-deps-to -`",
    )
    parser.add_argument(
        "--print-deps-to",
        "--print-deps-to-file",
        action="store",
        dest="print_deps_to",
        type=argparse.FileType('w'),
        metavar="FILE",
        default=None,
        help="Don't run tests, only print the dependencies to the given file "
            + "(use `-` for stdout)",
    )


@tox.hookimpl
def tox_configure(config):
    """Stores options in the config. Makes all commands external and skips sdist"""
    if config.option.print_deps_only:
        warnings.warn(
            "--print-deps-only is deprecated; use `--print-deps-to -`",
            DeprecationWarning,
        )
        if not config.option.print_deps_to:
            config.option.print_deps_to = sys.stdout
        else:
            raise tox.exception.ConfigError(
                "--print-deps-only cannot be used together "
                + "with --print-deps-to"
            )
    if config.option.current_env or config.option.print_deps_to:
        config.skipsdist = True
        for testenv in config.envconfigs:
            config.envconfigs[testenv].whitelist_externals = "*"

    return config


class InterpreterMismatch(tox.exception.InterpreterNotFound):
    """Interpreter version in current env does not match requested version"""


def _python_activate_exists(venv):
    python = venv.envconfig.get_envpython()
    bindir = os.path.dirname(python)
    activate = os.path.join(bindir, "activate")
    return os.path.exists(python), os.path.exists(activate)


def is_current_env_link(venv):
    python, activate = _python_activate_exists(venv)
    return python and not activate


def is_proper_venv(venv):
    python, activate = _python_activate_exists(venv)
    return python and activate


def is_any_env(venv):
    python, activate = _python_activate_exists(venv)
    return python


def rm_venv(venv):
    link = venv.envconfig.get_envpython()
    shutil.rmtree(os.path.dirname(os.path.dirname(link)), ignore_errors=True)


def unsupported_raise(config, venv):
    if config.option.recreate:
        return
    regular = not (config.option.current_env or config.option.print_deps_to)
    if regular and is_current_env_link(venv):
        if hasattr(tox.hookspecs, "tox_cleanup"):
            raise tox.exception.ConfigError(
                "Looks like previous --current-env or --print-deps-to tox run didn't finish the cleanup. "
                "Run tox run with --recreate (-r) or manually remove the environment in .tox."
            )
        else:
            raise tox.exception.ConfigError(
                "Regular tox run after --current-env or --print-deps-to tox run is not supported without --recreate (-r)."
            )
    elif config.option.current_env and is_proper_venv(venv):
        raise tox.exception.ConfigError(
            "--current-env after regular tox run is not supported without --recreate (-r)."
        )


@tox.hookimpl
def tox_testenv_create(venv, action):
    """We create a fake virtualenv with just the symbolic link"""
    config = venv.envconfig.config
    create_fake_env = check_version = config.option.current_env
    if config.option.print_deps_to:
        if is_any_env(venv):
            # We don't need anything
            return True
        else:
            # We need at least some kind of environment,
            # or tox fails without a python command
            # We fallback to --current-env behavior,
            # because it's cheaper, faster and won't install stuff
            create_fake_env = True
    if check_version:
        # With real --current-env, we check this, but not with --print-deps-to only
        version_info = venv.envconfig.python_info.version_info
        if version_info is None:
            raise tox.exception.InterpreterNotFound(venv.envconfig.basepython)
        if version_info[:2] != sys.version_info[:2]:
            raise InterpreterMismatch(
                f"tox_current_env: interpreter versions do not match:\n"
                + f"    in current env: {tuple(sys.version_info)}\n"
                + f"    requested: {version_info}"
            )
    if create_fake_env:
        # Make sure the `python` command on path is sys.executable.
        # (We might have e.g. /usr/bin/python3, not `python`.)
        # Remove the rest of the virtualenv.
        link = venv.envconfig.get_envpython()
        target = sys.executable
        rm_venv(venv)
        os.makedirs(os.path.dirname(link))
        if sys.platform == "win32":
            # Avoid requiring admin rights on Windows
            subprocess.check_call(f'mklink /J "{link}" "{target}"', shell=True)
        else:
            os.symlink(target, link)
        # prevent tox from creating the venv
        return True
    if not is_proper_venv(venv):
        rm_venv(venv)
    return None  # let tox handle the rest


@tox.hookimpl
def tox_package(session, venv):
    """Fail early when unsupported"""
    config = venv.envconfig.config
    unsupported_raise(config, venv)


@tox.hookimpl
def tox_testenv_install_deps(venv, action):
    """We don't install anything"""
    config = venv.envconfig.config
    unsupported_raise(config, venv)
    if config.option.current_env or config.option.print_deps_to:
        return True


@tox.hookimpl
def tox_runtest(venv, redirect):
    """If --print-deps-to, prints deps instead of running tests"""
    config = venv.envconfig.config
    unsupported_raise(config, venv)
    if config.option.print_deps_to:
        print(
            *venv.get_resolved_dependencies(),
            sep="\n",
            file=config.option.print_deps_to,
        )
        return True


@tox.hookimpl
def tox_cleanup(session):
    """Remove the fake virtualenv not to collide with regular tox
    Collisions can happen anyway (when tox is killed forcefully before this happens)
    Note that we don't remove real venvs, as recreating them is expensive"""
    for venv in session.venv_dict.values():
        if is_current_env_link(venv):
            rm_venv(venv)


@tox.hookimpl
def tox_runenvreport(venv, action):
    """Prevent using pip to display installed packages,
    use importlib.metadata instead, but fallback to default without our flags."""
    option = venv.envconfig.config.option
    if not (option.current_env or option.print_deps_only):
        return None
    return (
        "{}=={}".format(d.metadata.get("name"), d.version)
        for d in sorted(
            importlib_metadata.distributions(), key=lambda d: d.metadata.get("name")
        )
    )
