# Configuration Directory

This directory contains configuration files for local development.

## Generating querulus_config.json

To generate the full configuration from the Kubernetes templates:

```bash
cd ../loculus/kubernetes
helm template test-release loculus/ --set environment=server | \
  grep -A 1000000 "querulus_config.json:" | \
  tail -n +2 | \
  sed '/^---$/,$d' | \
  sed 's/^    //' > ../../config/querulus_config.json
```

This will extract the complete backend configuration including all organisms and their reference genomes.

## Note

The `querulus_config.json` file is gitignored since it's generated from the Kubernetes templates.
In production, this config is mounted from the `querulus-config` ConfigMap.
