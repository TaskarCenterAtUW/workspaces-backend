FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Change the working directory to the `app` directory
WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

# Copy the project into the image
ADD . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# set the environment variable for uvicorn to run the FastAPI app
ENV UV_PROJECT_ENVIRONMENT=.uvenv

# The commit this image was built from, reported to Sentry as the release.
# Set by the deploy workflow; declared last so a new commit doesn't
# invalidate the dependency layers above.
ARG CODE_VERSION=unknown
ENV CODE_VERSION=$CODE_VERSION

# Run with uvicorn
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
