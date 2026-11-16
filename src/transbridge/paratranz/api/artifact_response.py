# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Stream a project or task artifact response to its destination."""

from requests import Response


def save_artifact_response(response: Response, save_path: str, *, cancellation=None) -> str:
    try:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with open(save_path, "wb") as output:
            for chunk in response.iter_content(chunk_size=8192):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                output.write(chunk)
    finally:
        response.close()
    return save_path
