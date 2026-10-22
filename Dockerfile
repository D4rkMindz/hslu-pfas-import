FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
LABEL authors="Björn Pfoster;Boris Lauper"

# see https://github.com/astral-sh/uv-docker-example/blob/main/Dockerfile
# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Install the project into `/app`
WORKDIR /app

# set timezone
RUN ln -sf /usr/share/zoneinfo/Europe/Zurich /etc/localtime

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
# UV_COMPILE_BYTECODE: Enable bytecode compilation
# UV_LINK_MODE: Copy from the cache instead of linking since it's a mounted volume
# UV_NO_DEV: Omit development dependencies
# UV_TOOL_BIN_DIR: Ensure installed tools can be executed out of the box
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_TOOL_BIN_DIR=/usr/local/bin

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint, don't invoke `uv` (already in $PATH).
ENTRYPOINT ["pfas_import"]

# Use the non-root user to run our application
USER nonroot

CMD []