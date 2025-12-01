# Deployment Guide - DigitalOcean App Platform

This guide walks you through deploying the Restaurant Platform to DigitalOcean App Platform.

## Prerequisites

1. **DigitalOcean Account** with billing enabled
2. **GitHub Repository** connected to DigitalOcean
3. **doctl CLI** installed (optional, for command-line deployment)

## Quick Start

### Option 1: Deploy via DigitalOcean Dashboard

1. Go to [DigitalOcean App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App**
3. Select **GitHub** as the source
4. Select the `giorgigordiashvili/restaurant_platform` repository
5. Select the `main` branch
6. DigitalOcean will detect the `.do/app.yaml` configuration
7. Review and configure environment variables (see below)
8. Click **Create Resources**

### Option 2: Deploy via doctl CLI

```bash
# Install doctl
brew install doctl  # macOS
# or see: https://docs.digitalocean.com/reference/doctl/how-to/install/

# Authenticate
doctl auth init

# Create the app
doctl apps create --spec .do/app.yaml

# Get your app ID
doctl apps list

# Set secrets (replace YOUR_APP_ID)
doctl apps update YOUR_APP_ID --spec .do/app.yaml
```

## Required Environment Variables

Set these in the DigitalOcean App Platform dashboard under **Settings > App-Level Environment Variables**:

### Required Secrets (Mark as "Encrypt")

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | Generate with: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `MINIO_ACCESS_KEY` | DigitalOcean Spaces access key | Your Spaces access key |
| `MINIO_SECRET_KEY` | DigitalOcean Spaces secret key | Your Spaces secret key |
| `MINIO_ENDPOINT` | Spaces endpoint | `fra1.digitaloceanspaces.com` |

### Auto-Configured by App Platform

These are automatically set by DigitalOcean:
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `${APP_DOMAIN}` |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF | `https://${APP_DOMAIN}` |
| `CORS_ALLOWED_ORIGINS` | Allowed CORS origins | Empty |
| `MAIN_DOMAIN` | Main domain for tenant resolution | `localhost` |
| `MINIO_BUCKET_NAME` | Spaces bucket name | `restaurant-media` |
| `DEBUG` | Debug mode | `False` |

## Setting Up DigitalOcean Spaces (Object Storage)

1. Go to **Spaces** in DigitalOcean dashboard
2. Create a new Space (e.g., `restaurant-media`)
3. Choose a datacenter region (e.g., `fra1`)
4. Set the CDN endpoint (optional but recommended)
5. Go to **API > Spaces Keys** and generate access keys
6. Add the keys to your app environment variables

## GitHub Actions Setup

For automatic deployments, add these secrets to your GitHub repository:

1. Go to **Settings > Secrets and variables > Actions**
2. Add the following secrets:

| Secret | Description |
|--------|-------------|
| `DIGITALOCEAN_ACCESS_TOKEN` | DigitalOcean API token (generate at API > Tokens) |
| `DIGITALOCEAN_APP_ID` | Your app ID (from `doctl apps list`) |

## Deployment Workflow

1. **Push to `main` branch** triggers the deployment
2. **CI Pipeline** runs tests and linting
3. **CD Pipeline** deploys to DigitalOcean if CI passes
4. **Pre-deploy jobs** run migrations and collect static files
5. **App starts** with health checks

## Monitoring

### Health Checks

- **Health endpoint**: `GET /api/v1/health/` - Checks database and cache
- **Readiness endpoint**: `GET /api/v1/ready/` - Simple readiness check

### Logs

```bash
# View logs via doctl
doctl apps logs YOUR_APP_ID --type=run

# Or in the dashboard: Apps > Your App > Runtime Logs
```

### Metrics

DigitalOcean provides built-in metrics:
- CPU usage
- Memory usage
- HTTP request rates
- Response times

## Scaling

### Horizontal Scaling

Edit `.do/app.yaml` and change `instance_count`:

```yaml
services:
  - name: web
    instance_count: 2  # Increase for more instances
```

### Vertical Scaling

Change `instance_size_slug`:

```yaml
services:
  - name: web
    instance_size_slug: basic-xs  # Options: basic-xxs, basic-xs, basic-s, basic-m, etc.
```

## Database Management

### Running Migrations

Migrations run automatically on each deployment via the `migrate` job.

To run manually:

```bash
# Via doctl
doctl apps console YOUR_APP_ID web

# Then run:
python manage.py migrate
```

### Database Backups

DigitalOcean managed databases include automatic backups. Configure in:
**Databases > Your Database > Settings > Backups**

## Troubleshooting

### Common Issues

1. **502 Bad Gateway**
   - Check health endpoint: `/api/v1/health/`
   - Review application logs
   - Verify database connectivity

2. **Static files not loading**
   - Ensure `collectstatic` job completed
   - Check Spaces configuration

3. **Database connection errors**
   - Verify `DATABASE_URL` is set
   - Check database is running
   - Verify SSL settings (`sslmode=require`)

4. **Redis connection errors**
   - Verify `REDIS_URL` is set
   - Check Redis is running

### Getting Help

- [DigitalOcean App Platform Docs](https://docs.digitalocean.com/products/app-platform/)
- [Django Deployment Checklist](https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/)

## Cost Estimation

Basic setup (for development/staging):
- **Web service**: Basic XXS ($5/mo)
- **Celery worker**: Basic XXS ($5/mo)
- **Celery beat**: Basic XXS ($5/mo)
- **PostgreSQL**: Dev Database ($7/mo)
- **Redis**: Dev Database ($10/mo)
- **Spaces**: $5/mo (250GB storage)

**Total**: ~$37/month

For production, scale up instances as needed.
