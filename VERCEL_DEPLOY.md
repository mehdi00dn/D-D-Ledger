# Vercel deployment notes

## Test deployment

This project now has a Vercel Python entry point at `api/index.py` and a `vercel.json` routing configuration.

Set these Vercel environment variables:

- `SECRET_KEY` = v7Kx9mQ2pL8zR4tN6wY1sF3hJ5cB0dE8aG2uP9xV6kM4qT7n

Then deploy the repository.

## Important limitation

The current application uses SQLite and local file uploads. Vercel's serverless filesystem is ephemeral, so data written to SQLite or `/tmp` uploads is **not persistent** across cold starts/redeployments and may not be shared between concurrent instances.

This configuration is intended to get the app running for a test/demo deployment while preserving the current application code as much as possible.

For a real multi-user deployment, migrate:

- SQLite -> PostgreSQL (or another hosted relational database)
- `uploads/` -> Vercel Blob, S3-compatible storage, Cloudinary, etc.

The existing bundled `dnd.db` is copied to the Vercel writable runtime on cold start so the current sample data can be displayed.
