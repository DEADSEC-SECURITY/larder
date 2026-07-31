import ssl
from unittest.mock import patch

from larder.db import connection_options


def test_asyncpg_tls_query_is_translated_to_ssl_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with patch("larder.db.ssl.create_default_context", return_value=context) as create_context:
        url, connect_args = connection_options(
            "postgresql+asyncpg://user:pass@db/larder"
            "?sslmode=verify-full&sslrootcert=C:/certs/household-ca.crt"
        )

    assert "sslmode" not in url.query
    assert "sslrootcert" not in url.query
    assert connect_args["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert connect_args["ssl"].check_hostname is True
    create_context.assert_called_once_with(cafile="C:/certs/household-ca.crt")


def test_asyncpg_require_removes_libpq_query_parameters():
    url, connect_args = connection_options(
        "postgresql+asyncpg://user:pass@db/larder?sslmode=require"
    )

    assert "sslmode" not in url.query
    assert connect_args["ssl"].verify_mode == ssl.CERT_NONE
    assert connect_args["ssl"].check_hostname is False
