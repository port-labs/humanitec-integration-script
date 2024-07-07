import httpx
from typing import Any, Dict
from loguru import logger
from typing import List, Dict, Optional, Union
from pydantic import BaseModel, create_model, Field
from .cache import InMemoryCache


class PortClient:
    def __init__(self, client_id, client_secret, **kwargs) -> None:
        self.httpx_async_client = kwargs.get("httpx_async_client", httpx.AsyncClient())
        self.client_id = client_id
        self.cache = InMemoryCache()
        self.client_secret = client_secret
        self.base_url = kwargs.get("base_url", "https://api.getport.io/v1")
        self.port_headers = None

    async def get_port_access_token(self) -> str:
        credentials = {"clientId": self.client_id, "clientSecret": self.client_secret}
        endpoint = f"/auth/access_token"
        response = await self.send_api_request("POST", endpoint, json=credentials)
        access_token = response["accessToken"]
        return access_token

    async def get_port_headers(self) -> Dict[str, str]:
        access_token = await self.get_port_access_token()
        port_headers = {"Authorization": f"Bearer {access_token}"}
        return port_headers

    async def send_api_request(
        self,
        method: str,
        endpoint: str,
        headers: Dict[str, str] | None = None,
        json: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        try:
            response = await self.httpx_async_client.request(
                method, url, headers=headers, json=json
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            raise

    async def upsert_entity(
        self, blueprint_id: str, entity_object: Dict[str, Any]
    ) -> None:
        endpoint = f"/blueprints/{blueprint_id}/entities?upsert=true&merge=true"
        port_headers = (
            self.port_headers if self.port_headers else await self.get_port_headers()
        )
        response = await self.send_api_request(
            "POST", endpoint, headers=port_headers, json=entity_object
        )
        logger.info(response)
        return response

    # async def upsert_entity(
    #     self, blueprint_id: str, entity_object: Dict[str, Any]
    # ) -> None:

    #     model:BaseModel = await self.create_dynamic_model(blueprint_id)
    #     model = model(**entity_object)
    #     print("MODLE OBJECT",model.json())
    #     endpoint = f"/blueprints/{blueprint_id}/entities?upsert=true&merge=true"
    #     port_headers = (
    #         self.port_headers if self.port_headers else await self.get_port_headers()
    #     )
    #     response = await self.send_api_request(
    #         "POST", endpoint, headers=port_headers, json=model.json()
    #     )
    #     logger.info(response)
    #     return response

    async def read_blueprint(self, blueprint_id: str):
        endpoint = f"/blueprints/{blueprint_id}"
        port_headers = (
            self.port_headers if self.port_headers else await self.get_port_headers()
        )
        response = await self.send_api_request("GET", endpoint, headers=port_headers)
        logger.info(response)
        return response

    @staticmethod
    def create_property_model(schema: Dict[str, Any], required: List[str]) -> BaseModel:
        fields = {}
        for field_name, field_props in schema.items():
            field_type = field_props.get("type")
            default_value = field_props.get("default", None)

            if field_type == "string":
                pydantic_type = str
            elif field_type == "integer":
                pydantic_type = int
            elif field_type == "number":
                pydantic_type = float
            elif field_type == "boolean":
                pydantic_type = bool
            elif field_type == "array":
                pydantic_type = List[Union[str, int, float]]
            elif field_type == "object":
                pydantic_type = Dict[str, Any]
            else:
                pydantic_type = Any

            if field_name in required:
                fields[field_name] = (
                    pydantic_type,
                    Field(
                        title=field_props.get("title"),
                        description=field_props.get("description"),
                    ),
                )
            else:
                fields[field_name] = (
                    Optional[pydantic_type],
                    Field(
                        default=default_value,
                        title=field_props.get("title"),
                        description=field_props.get("description"),
                    ),
                )

            # Handle enums
            if "enum" in field_props:
                fields[field_name] = (
                    Optional[Union[pydantic_type, str]],
                    Field(
                        default=default_value,
                        title=field_props.get("title"),
                        description=field_props.get("description"),
                        enum=field_props["enum"],
                    ),
                )

        model = create_model("property", **fields)
        return model

    async def create_dynamic_model(self, identifier: str) -> BaseModel:

        if model_fields := await self.cache.get(identifier):
            print(f"Retrieved {identifier} blueprint model from cache")
            model = create_model(identifier, **model_fields)
            return model

        blueprint = await self.read_blueprint(identifier)

        schema = blueprint.get("schema", {}).get("properties", {})
        required = blueprint.get("schema", {}).get("required", [])

        property_model = self.create_property_model(schema, required)

        fields = {
            "identifier": (str, ...),
            "title": (str, ...),
            "properties": (property_model, ...),
            "relations": (Dict[str, Any], ...),
        }

        model = create_model(identifier, **fields)
        await self.cache.set(identifier, fields)
        logger.info(f"Cached {identifier} blueprint model")
        return model
