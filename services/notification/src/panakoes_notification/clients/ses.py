"""Thin SES `send_email` wrapper.

The class exists so tests can swap the boto3 client for a moto-backed
fake without monkeypatching at module level. Production callers pass
`None` and let boto3 resolve the default credential chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import boto3

if TYPE_CHECKING:
    from mypy_boto3_ses.client import SESClient
    from mypy_boto3_ses.type_defs import BodyTypeDef


class SESEmailSender:
    """Send transactional emails via Amazon SES."""

    def __init__(
        self,
        from_address: str,
        region_name: str = "us-east-1",
        client: SESClient | None = None,
    ) -> None:
        """Bind to the configured `from_address` and (optional) client."""
        self._from_address = from_address
        self._region_name = region_name
        self._client = (
            client if client is not None else boto3.client("ses", region_name=region_name)
        )

    @property
    def from_address(self) -> str:
        """The verified SES sender address used as `Source`."""
        return self._from_address

    def send(
        self,
        *,
        to_address: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> str:
        """Send an email. Returns the SES `MessageId` on success.

        Both `text_body` (always present) and `html_body` (optional)
        are passed through; SES delivers a multipart/alternative when
        both are set.
        """
        body: dict[str, Any] = {"Text": {"Data": text_body, "Charset": "UTF-8"}}
        if html_body is not None:
            body["Html"] = {"Data": html_body, "Charset": "UTF-8"}
        response = self._client.send_email(
            Source=self._from_address,
            Destination={"ToAddresses": [to_address]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": cast("BodyTypeDef", body),
            },
        )
        message_id: str = response["MessageId"]
        return message_id
