#!/usr/bin/env bash
# Run tests/clinic against a throwaway PostgreSQL container.
#
# Own container, own network, own ephemeral loopback port, random password. It
# never connects to any other database. Container and network are removed on
# exit, also after a failure. Extra arguments go to pytest.
#
#   scripts/clinic_pg_run.sh [pytest args]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(cd "$ROOT/../../.." && pwd)/venv/bin/python"
IMAGE="${CLINIC_PG_IMAGE:-postgres:16-alpine}"
SUFFIX="$(date +%s)-$$"
NAME="clinic-pgtest-$SUFFIX"
NET="clinic-pgtest-net-$SUFFIX"
PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"

cleanup() {
    docker rm -f -v "$NAME" >/dev/null 2>&1
    docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT

docker network create --subnet "${CLINIC_PG_SUBNET:-10.251.213.0/28}" "$NET" >/dev/null || exit 2
docker run -d --name "$NAME" --network "$NET" -p 127.0.0.1::5432 \
    -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_USER=clinic -e POSTGRES_DB=clinic_test \
    "$IMAGE" >/dev/null || exit 2

PORT="$(docker port "$NAME" 5432/tcp | head -n1 | sed 's/.*://')"
for _ in $(seq 1 60); do
    docker exec "$NAME" pg_isready -U clinic -d clinic_test >/dev/null 2>&1 && break
    sleep 1
done
# pg_isready answers during the init restart; wait until a real query works
for _ in $(seq 1 30); do
    docker exec "$NAME" psql -U clinic -d clinic_test -tAc 'select 1' >/dev/null 2>&1 && break
    sleep 1
done

echo "postgres: $(docker exec "$NAME" psql -U clinic -d clinic_test -tAc 'show server_version') on 127.0.0.1:$PORT (container $NAME)"

PYTEST_DATABASE_URI="postgresql+psycopg2://clinic:$PASSWORD@127.0.0.1:$PORT/clinic_test" \
    "$PYTHON" -m pytest "$ROOT/tests/clinic" -q -p no:cacheprovider --rootdir "$ROOT" "$@"
STATUS=$?

cleanup
trap - EXIT
if docker ps -a --format '{{.Names}}' | grep -q "^$NAME$"; then
    echo "cleanup failed: container $NAME still exists"
    exit 3
fi
echo "removed container $NAME and network $NET"
exit $STATUS
