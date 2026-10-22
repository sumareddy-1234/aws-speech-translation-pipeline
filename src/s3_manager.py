"""
S3 upload, URI generation, and cleanup utilities.
"""
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .aws_clients import get_s3_client

logger = logging.getLogger(__name__)


class S3Manager:
    """Manages S3 staging operations for the speech pipeline."""

    def __init__(self, bucket_name: str):
        if not bucket_name:
            raise ValueError("S3 bucket name must not be empty.")
        self.bucket_name = bucket_name
        self._client = get_s3_client()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, object_name: str) -> str:
        """
        Upload a local file to S3 and return its S3 URI.

        Args:
            local_path: Absolute or relative path to the local audio file.
            object_name: Destination key in the S3 bucket.

        Returns:
            S3 URI string in the form ``s3://<bucket>/<key>``.

        Raises:
            FileNotFoundError: If *local_path* does not exist.
            RuntimeError: On AWS API errors.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {local_path}")

        logger.info("Uploading '%s' to s3://%s/%s", local_path, self.bucket_name, object_name)
        try:
            self._client.upload_file(str(path), self.bucket_name, object_name)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(
                f"Failed to upload '{local_path}' to S3 bucket '{self.bucket_name}': {exc}"
            ) from exc

        uri = f"s3://{self.bucket_name}/{object_name}"
        logger.info("Upload complete: %s", uri)
        return uri

    def delete_file(self, object_name: str) -> None:
        """
        Delete a staged object from S3.

        Errors are logged but **not** re-raised so cleanup never conceals the
        primary pipeline failure.

        Args:
            object_name: The S3 key to delete.
        """
        logger.info("Deleting s3://%s/%s", self.bucket_name, object_name)
        try:
            self._client.delete_object(Bucket=self.bucket_name, Key=object_name)
            logger.info("Deleted s3://%s/%s", self.bucket_name, object_name)
        except (BotoCoreError, ClientError) as exc:
            logger.warning(
                "Cleanup warning — could not delete s3://%s/%s: %s",
                self.bucket_name,
                object_name,
                exc,
            )
