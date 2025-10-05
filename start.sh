#!/bin/bash
uvicorn CBot:app --host 0.0.0.0 --port ${PORT:-10000}
