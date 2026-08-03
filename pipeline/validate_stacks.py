#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Check that pipeline/stacks.yml, the templates on disk, and pipeline.yml all agree.

Run this through `tools/check` rather than directly -- the inline script metadata above lets
`uv run` fetch pyyaml on demand, so there is nothing to install and no venv to activate.

Run with no arguments to validate; run with --list to print the registered template paths
(what PR checks feeds to cfn-lint).

Two invariants, each checked in both directions:

registry <-> filesystem
    A template nobody registered is a template nobody lints, and a registry entry with no
    file is a stack the pipeline fails to deploy at merge time rather than at review time.

registry <-> pipeline.yml
    A `deployed_by: pipeline` entry with no CloudFormation action deploys nothing, and does
    so *silently* -- PR checks pass, every pipeline stage reports Succeeded, and no stack
    appears. Adding the action is step 3 of "Adding a blueprint stack" in pipeline/README.md;
    this is what makes forgetting it a review-time error instead of a mystery.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / 'pipeline' / 'stacks.yml'
PIPELINE_PATH = REPO_ROOT / 'pipeline' / 'pipeline.yml'

# CloudFormation deploy actions name their template as
# `TemplatePath: 'GitRepositoryArtifact::<repo-relative-path>'`. Matched by text scan for the
# same reason discover_templates() uses one: pipeline.yml is full of CloudFormation short tags
# (!Sub, !GetAtt) that yaml.safe_load cannot parse without a custom loader.
TEMPLATE_PATH_PATTERN = re.compile(r'GitRepositoryArtifact::([^\s\'"]+)')

# A YAML file is a CloudFormation template if it declares a template format version. Cheap
# text scan rather than a YAML parse, because CloudFormation short tags (!Sub, !GetAtt)
# are not loadable by yaml.safe_load without a custom loader.
TEMPLATE_MARKER = 'AWSTemplateFormatVersion'

# Directories with no deployable templates in them.
SKIP_DIRS = {'.git', '.github', 'node_modules', '.venv', '__pycache__'}

VALID_DEPLOYED_BY = {'pipeline', 'manual'}


def discover_templates() -> set[str]:
    """Every CloudFormation template in the repo, as repo-relative posix paths."""
    found = set()
    for path in REPO_ROOT.rglob('*'):
        if path.suffix not in {'.yml', '.yaml'} or not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        try:
            if TEMPLATE_MARKER in path.read_text(encoding='utf-8'):
                found.add(path.relative_to(REPO_ROOT).as_posix())
        except UnicodeDecodeError:
            continue
    return found


def pipeline_deployed_templates() -> set[str]:
    """Every template path a CloudFormation action in pipeline/pipeline.yml deploys."""
    if not PIPELINE_PATH.is_file():
        return set()
    text = PIPELINE_PATH.read_text(encoding='utf-8')
    return set(TEMPLATE_PATH_PATTERN.findall(text))


def load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        sys.exit(f'missing registry: {REGISTRY_PATH.relative_to(REPO_ROOT)}')
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding='utf-8')) or {}
    entries = registry.get('templates')
    if not isinstance(entries, list) or not entries:
        sys.exit('pipeline/stacks.yml must define a non-empty "templates" list')
    return entries


def validate(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    # template path -> deployed_by, for the pipeline.yml cross-check below.
    declared: dict[str, str] = {}

    for index, entry in enumerate(entries):
        where = f'templates[{index}]'
        if not isinstance(entry, dict):
            errors.append(f'{where}: expected a mapping, got {type(entry).__name__}')
            continue

        name = entry.get('name')
        template = entry.get('template')
        deployed_by = entry.get('deployed_by')

        if not name:
            errors.append(f'{where}: missing "name"')
        elif name in seen_names:
            errors.append(f'{where}: duplicate name {name!r}')
        else:
            seen_names.add(name)

        if deployed_by not in VALID_DEPLOYED_BY:
            errors.append(
                f'{where} ({name}): deployed_by must be one of '
                f'{sorted(VALID_DEPLOYED_BY)}, got {deployed_by!r}'
            )

        if not template:
            errors.append(f'{where} ({name}): missing "template"')
            continue
        if template in seen_paths:
            errors.append(f'{where} ({name}): duplicate template path {template!r}')
        seen_paths.add(template)
        declared[template] = deployed_by

        path = REPO_ROOT / template
        if not path.is_file():
            errors.append(f'{where} ({name}): registered template does not exist: {template}')
        elif TEMPLATE_MARKER not in path.read_text(encoding='utf-8'):
            errors.append(
                f'{where} ({name}): {template} has no {TEMPLATE_MARKER} '
                'and so is not a CloudFormation template'
            )

    for orphan in sorted(discover_templates() - seen_paths):
        errors.append(
            f'unregistered CloudFormation template: {orphan} '
            '-- add it to pipeline/stacks.yml so PR checks lint it'
        )

    errors.extend(check_pipeline_actions(declared))

    return errors


def check_pipeline_actions(declared: dict[str, str]) -> list[str]:
    """Cross-check the registry against the CloudFormation actions in pipeline/pipeline.yml.

    Catches the silent failure: a blueprint registered as `deployed_by: pipeline` with no
    action deploys nothing while every check and every pipeline stage still reports success.
    """
    errors: list[str] = []
    deployed = pipeline_deployed_templates()

    for template, deployed_by in sorted(declared.items()):
        if deployed_by == 'pipeline' and template not in deployed:
            errors.append(
                f'{template} is registered deployed_by: pipeline but no CloudFormation '
                'action in pipeline/pipeline.yml deploys it -- it would deploy nothing, '
                'silently. Add a BlueprintDeploy action (step 3 of "Adding a blueprint '
                'stack" in pipeline/README.md), or register it as deployed_by: manual.'
            )
        if deployed_by == 'manual' and template in deployed:
            errors.append(
                f'{template} is registered deployed_by: manual but pipeline/pipeline.yml '
                'deploys it -- change deployed_by to pipeline, or remove the action.'
            )

    for template in sorted(deployed - set(declared)):
        errors.append(
            f'pipeline/pipeline.yml deploys {template}, which is not in pipeline/stacks.yml '
            '-- register it so PR checks lint it before it reaches a deploy.'
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--list',
        action='store_true',
        help='print registered template paths, one per line, instead of validating',
    )
    args = parser.parse_args()

    entries = load_registry()

    if args.list:
        for entry in entries:
            if isinstance(entry, dict) and entry.get('template'):
                print(entry['template'])
        return 0

    errors = validate(entries)
    if errors:
        print(f'pipeline/stacks.yml: {len(errors)} problem(s)', file=sys.stderr)
        for error in errors:
            print(f'  - {error}', file=sys.stderr)
        return 1

    print(f'pipeline/stacks.yml: {len(entries)} template(s) registered and present')
    deployed = pipeline_deployed_templates()
    for entry in entries:
        # Showing which templates an action actually deploys makes step 3 of "Adding a
        # blueprint stack" visible rather than something you find out by its absence.
        wired = ' <- deployed by a pipeline action' if entry['template'] in deployed else ''
        print(f"  {entry['deployed_by']:<9} {entry['template']}{wired}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
