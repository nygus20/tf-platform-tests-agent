#!/bin/sh
# Prints the current revision as JSON, for the external data source.
revision=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
printf '{"revision":"%s"}\n' "$revision"
