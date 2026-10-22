"""
Boilerplate boto3 client initialisation.
Reads credentials from environment variables; never hardcodes secrets.
"""
import os
import boto3
from botocore.config import Config


def _region() -> str:
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return region


def get_s3_client():
    """Return a configured S3 client."""
    return boto3.client("s3", region_name=_region())


def get_transcribe_client():
    """Return a configured Transcribe client."""
    return boto3.client("transcribe", region_name=_region())


def get_translate_client():
    """Return a configured Translate client."""
    return boto3.client("translate", region_name=_region())


def get_polly_client():
    """Return a configured Polly client."""
    return boto3.client("polly", region_name=_region())
