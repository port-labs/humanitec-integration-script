import httpx
import asyncio
from typing import Dict, Any, List
import datetime
import re
from loguru import logger
from .cache import InMemoryCache


class CACHE_KEYS:
    APPLICATION = "APPLICATION_CACHE_KEY"
    ENVIRONMENT = "ENVIRONMENT_CACHE_KEY"
    RESOURCE = "RESOURCE_CACHE_KEY"


class HumanitecClient:
    def __init__(self, org_id, api_token, **kwargs) -> None:
        self.client = kwargs.get("httpx_async_client", httpx.AsyncClient())
        self.base_url = (
            f"{kwargs.get('base_url','https://api.humanitec.io')}/orgs/{org_id}/"
        )
        self.api_token = api_token
        self.cache = InMemoryCache()
        self.port_headers = None

    def get_humanitec_headers(self) -> Dict[str, str]:
        humanitec_headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        return humanitec_headers

    async def send_api_request(
        self,
        method: str,
        endpoint: str,
        headers: Dict[str, str] | None = None,
        json: Dict[str, Any] | List[Dict[str, Any]] | None = None,
    ) -> Any:
        url = self.base_url + endpoint
        try:
            logger.debug(f"Requesting Humanitec data for endpoint: {endpoint}")
            response = await self.client.request(
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

    async def get_all_applications(self) -> List[Dict[str, Any]]:
        if cached_applications := await self.cache.get(CACHE_KEYS.APPLICATION):
            logger.info(f"Retrieved {len(cached_applications)} applications from cache")
            return list(cached_applications.values())

        endpoint = "apps"
        humanitec_headers = self.get_humanitec_headers()
        applications: List[Dict[str, Any]] = await self.send_api_request(
            "GET", endpoint, headers=humanitec_headers
        )

        await self.cache.set(
            CACHE_KEYS.APPLICATION, {app["id"]: app for app in applications}
        )
        logger.info(f"Received {len(applications)} applications from Humanitec")

        return applications

    async def get_all_environments(self, app) -> List[Dict[str, Any]]:

        try:
            if cached_environments := await self.cache.get(CACHE_KEYS.ENVIRONMENT):
                if app_environments := cached_environments.get(app["id"]):
                    logger.info(
                        f"Retrieved {len(app_environments)} environment for {app['id']} from cache"
                    )
                    return list(app_environments.values())

            logger.info("Fetching environments from Humanitec")

            endpoint = f"apps/{app['id']}/envs"
            humanitec_headers = self.get_humanitec_headers()
            environments: List[Dict[str, Any]] = await self.send_api_request(
                "GET", endpoint, headers=humanitec_headers
            )
            await self.cache.set(
                CACHE_KEYS.ENVIRONMENT,
                {
                    app["id"]: {
                        environment["id"]: environment for environment in environments
                    }
                },
            )
            logger.info(f"Received {len(environments)} environments from Humanitec")
            return environments
        except Exception as e:
            logger.error(f"Failed to fetch environments from {app['id']}: {str(e)}")
            return []

    async def get_all_resources(self, app, env) -> List[Dict[str, Any]]:
        try:
            if cached_resources := await self.cache.get(CACHE_KEYS.RESOURCE):
                if env_resources := cached_resources.get(app["id"], {}).get(
                    env["id"]
                ):
                    logger.info(
                        f"Retrieved {len(env_resources)} resources from cache for app {app['id']} and env {env['id']}"
                    )
                    return list(env_resources.values())

            logger.info("Fetching resources from Humanitec")
            endpoint = f"apps/{app['id']}/envs/{env['id']}/resources"
            humanitec_headers = self.get_humanitec_headers()
            resources: List[Dict[str, Any]] = await self.send_api_request(
                "GET", endpoint, headers=humanitec_headers
            )
            await self.cache.set(
                CACHE_KEYS.RESOURCE,
                {
                    app["id"]: {
                        env["id"]: {
                            resource["gu_res_id"]: resource for resource in resources
                        }
                    }
                },
            )
            logger.info(
                f"Received {len(resources)} resources for {env['id']} environment in {app['id']}"
            )
            return resources
        except Exception as e:
            logger.error(
                f"Failed to fetch resources for {env['id']} environment in {app[id]}: {str(e)}"
            )
            return []

    async def get_dependency_graph(
        self, app: Dict[str, Any], env: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        try:
            if dependency_graph_id := env.get("last_deploy", {}).get("dependency_graph_id"):
                endpoint = f"apps/{app['id']}/envs/{env['id']}/resources/graphs/{dependency_graph_id}"
                humanitec_headers = self.get_humanitec_headers()
                graph = await self.send_api_request(
                    "GET", endpoint, headers=humanitec_headers
                )
                nodes = graph["nodes"]
                logger.info(
                    f"Received {len(nodes)} graph nodes for {env['id']} environment in {app['id']}"
                )
                return nodes

            logger.info(
                f"No dependency graph found for {env['id']} environment in {app['id']}"
            )
            return []
        except Exception as e:
            logger.error(
                f"Failed to fetch dependency graphs for {env['id']} environment in {app['id']}: {str(e)}"
            )
            return []

    async def get_resource_graph(
        self, app: Dict[str, Any], env: Dict[str, Any], data: List[Dict[str, Any]]
    ) -> Any:
        endpoint = f"apps/{app['id']}/envs/{env['id']}/resources/graph"
        humanitec_headers = self.get_humanitec_headers()
        graph = await self.send_api_request(
            "POST", endpoint, headers=humanitec_headers, json=data
        )
        return graph

    def group_resources_by_type(
        self, data: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        grouped_resources: dict[str, Any] = {}
        for resource in data:
            workload_id = resource["res_id"].split(".")[0]
            if workload_id not in grouped_resources:
                grouped_resources[workload_id] = []
            grouped_resources[workload_id].append(resource)
        return grouped_resources
