# Repository Guidelines

## Project Structure & Module Organization

This repository is currently an empty scaffold. As the project takes shape, keep application code in clearly named source directories (such as `src/`), tests beside their modules or under `tests/`, and static files under `assets/`. Group related functionality into small modules and document any non-obvious top-level directory here.

## Build, Test, and Development Commands

No build system or development commands are defined yet. When adding one, document the canonical setup, run, and test commands in the README and keep them reproducible from the repository root. For services intended to run in this environment, prefer a checked-in `compose.yaml` and standard `docker compose` commands; keep local-only values out of version control.

## Coding Style & Naming Conventions

Follow the formatter and linter selected for each language, and commit their configuration with the code. Use descriptive, consistent names: `snake_case` where idiomatic in Python, `camelCase` for JavaScript variables and functions, and `PascalCase` for types and components. Keep modules focused and avoid adding dependencies without a clear need.

## Testing Guidelines

There is no test framework or coverage target yet. Add tests with each behavior change, use names that describe the behavior under test, and provide one documented command for running the relevant suite. Include regression coverage when fixing a bug.

## Commit & Pull Request Guidelines

No Git history is available to establish a commit convention. Use short, imperative commit subjects (for example, `Add compose health check`). Pull requests should explain the change and motivation, list validation commands and results, link related issues, and include screenshots for visible UI changes.

## Security & Configuration

Never commit credentials, tokens, private keys, or environment-specific secret URLs. Provide safe example configuration where needed, and load real secrets through environment variables or an untracked local env file.

## Builder and Runtime Feature Parity

The core goal of this project is the game engine and its builder. Treat each exported game runtime as configured output from the builder. Any feature added to a specific game's runtime or to the shared runtime base container must have appropriate configuration options in the builder, stored with the world or its content and carried into exports. Avoid runtime-only hard-coded behavior; provide a sensible editable default when sample content needs the feature. Keep authoring, validation, playtesting, and export behavior aligned.
