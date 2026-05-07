"""
VoiceInsight AI — Storage Service
Abstracts object storage (S3-compatible) and local file storage.
"""

import os
import logging
import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings

logger = logging.getLogger("voiceinsight.storage")

class StorageService:
    def __init__(self):
        self.settings = get_settings()
        self.is_s3_enabled = bool(self.settings.S3_BUCKET_NAME and self.settings.AWS_ACCESS_KEY_ID)
        
        if self.is_s3_enabled:
            client_kwargs = {
                "aws_access_key_id": self.settings.AWS_ACCESS_KEY_ID,
                "aws_secret_access_key": self.settings.AWS_SECRET_ACCESS_KEY,
                "region_name": self.settings.AWS_REGION,
            }
            if self.settings.S3_ENDPOINT_URL:
                client_kwargs["endpoint_url"] = self.settings.S3_ENDPOINT_URL
                
            self.s3_client = boto3.client("s3", **client_kwargs)
            logger.info("StorageService initialized with S3-compatible backend.")
        else:
            logger.info("StorageService initialized with local disk backend.")

    def upload_file(self, local_file_path: str, destination_name: str, content_type: str = "audio/wav") -> str:
        """
        Uploads a file to S3 or copies it locally depending on config.
        Returns the URL or relative path to be stored in the DB.
        """
        if self.is_s3_enabled:
            try:
                self.s3_client.upload_file(
                    local_file_path, 
                    self.settings.S3_BUCKET_NAME, 
                    destination_name,
                    ExtraArgs={"ContentType": content_type}
                )
                logger.info("Uploaded %s to S3 bucket %s", destination_name, self.settings.S3_BUCKET_NAME)
                # Return the API path so the frontend fetches from the backend, 
                # and the backend redirects to a presigned S3 URL
                if destination_name.startswith("raw/"):
                    return f"/v1/recordings/{os.path.basename(destination_name)}"
                elif destination_name.startswith("snippets/"):
                    return f"/v1/snippets/{os.path.basename(destination_name)}"
                return f"/{destination_name}"
            except ClientError as e:
                logger.error("Failed to upload %s to S3: %s", destination_name, e)
                raise
        else:
            # Local storage: return URL matching the serve_recording / serve_snippet endpoints
            # which only capture the bare filename (no subdirectory prefix).
            filename = os.path.basename(destination_name)
            if destination_name.startswith("snippets/"):
                return f"/v1/snippets/{filename}"
            return f"/v1/recordings/{filename}"

    def generate_presigned_url(self, object_name: str, expiration: int = 3600) -> str:
        """
        Generates a presigned URL for an S3 object.
        If using local storage, returns the local API path.
        """
        if not self.is_s3_enabled:
            return f"/v1/recordings/{object_name}"
            
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.S3_BUCKET_NAME, "Key": object_name},
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            logger.error("Failed to generate presigned URL for %s: %s", object_name, e)
            return ""

def get_storage_service() -> StorageService:
    return StorageService()
