"""Client used by the demo service to talk to the Petstore API."""

from __future__ import annotations

import httpx

BASE_URL = "https://api.petstore.dev/v1"


class PetstoreClient:
    def __init__(self, token: str) -> None:
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )

    def list_pets(self, status: str = "available") -> list[dict]:
        response = self._http.get("/pets", params={"status": status, "limit": 50})
        response.raise_for_status()
        return response.json()

    def get_pet(self, pet_id: str) -> dict:
        response = self._http.get(f"/pets/{pet_id}")
        response.raise_for_status()
        return response.json()

    def delete_pet(self, pet_id: str) -> None:
        response = self._http.delete(f"/pets/{pet_id}")
        response.raise_for_status()

    def owner(self, owner_id: str) -> dict:
        response = self._http.get(f"/v1/owners/{owner_id}")
        response.raise_for_status()
        return response.json()