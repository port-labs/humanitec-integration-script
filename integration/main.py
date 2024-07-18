import asyncio
import argparse
import time
import datetime
from decouple import config  # type: ignore
import re
import asyncio
from loguru import logger
from clients.humanitec_client import HumanitecClient
from clients.port_client import PortClient
import httpx


class BLUEPRINT:
    APPLICATION = "humanitecApplication"
    ENVIRONMENT = "humanitecEnvironment"
    WORKLOAD = "humanitecWorkload"
    RESOURCE_GRAPH = "humanitecResourceGraph"
    RESOURCE = "humanitecResource"


class HumanitecExporter:
    def __init__(self, args) -> None:

        timeout = httpx.Timeout(10.0, connect=10.0, read=20.0, write=10.0)
        httpx_async_client = httpx.AsyncClient(timeout=timeout)
        self.port_client = PortClient(
            args.port_client_id,
            args.port_client_secret,
            httpx_async_client=httpx_async_client,
        )
        self.humanitec_client = HumanitecClient(
            args.org_id,
            args.api_key,
            api_url=args.api_url,
            httpx_async_client=httpx_async_client,
        )

    @staticmethod
    def convert_to_datetime(timestamp: int) -> str:
        converted_datetime = datetime.datetime.fromtimestamp(
            timestamp / 1000.0, datetime.timezone.utc
        )
        return converted_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def remove_symbols_and_title_case(input_string: str) -> str:
        cleaned_string = re.sub(r"[^A-Za-z0-9\s]", " ", input_string)
        title_case_string = cleaned_string.title()
        return title_case_string

    async def sync_applications(self) -> None:
        logger.info(f"Syncing entities for blueprint {BLUEPRINT.APPLICATION}")
        applications = await self.humanitec_client.get_all_applications()

        def create_entity(application):
            return {
                "identifier": application["id"],
                "title": self.remove_symbols_and_title_case(application["name"]),
                "properties": {"createdAt": application["created_at"]},
                "relations": {},
            }

        tasks = [
            self.port_client.upsert_entity(
                blueprint_id=BLUEPRINT.APPLICATION,
                entity_object=create_entity(application),
            )
            for application in applications
        ]

        await asyncio.gather(*tasks)
        logger.info(f"Finished syncing entities for blueprint {BLUEPRINT.APPLICATION}")

    async def sync_environments(self) -> None:
        logger.info(f"Syncing entities for blueprint {BLUEPRINT.ENVIRONMENT}")
        applications = await self.humanitec_client.get_all_applications()

        def create_entity(application, environment):
            return {
                "identifier": f"{application['id']}/{environment['id']}",
                "title": environment["name"],
                "properties": {
                    "type": environment["type"],
                    "createdAt": environment["created_at"],
                    "lastDeploymentStatus": environment.get("last_deploy", {}).get(
                        "status"
                    ),
                    "lastDeploymentDate": environment.get("last_deploy", {}).get(
                        "created_at"
                    ),
                    "lastDeploymentComment": environment.get("last_deploy", {}).get(
                        "comment"
                    ),
                },
                "relations": {BLUEPRINT.APPLICATION: application["id"]},
            }

        tasks = [
            self.port_client.upsert_entity(
                blueprint_id=BLUEPRINT.ENVIRONMENT,
                entity_object=create_entity(application, environment),
            )
            for application in applications
            for environments in [
                await self.humanitec_client.get_all_environments(application)
            ]
            for environment in environments
        ]
        await asyncio.gather(*tasks)
        logger.info(f"Finished syncing entities for blueprint {BLUEPRINT.ENVIRONMENT}")

    async def sync_workloads(self):
        logger.info(f"Syncing entities for blueprint {BLUEPRINT.WORKLOAD}")

        def create_workload_entity(resource, application):
            identifier = f"{application['id']}/{environment['id']}/{resource['res_id'].replace('modules.', '')}"
            return {
                "identifier": identifier,
                "title": self.remove_symbols_and_title_case(
                    resource["res_id"].replace("modules.", "")
                ),
                "properties": {
                    "status": resource["status"],
                    "class": resource["class"],
                    "driverType": resource["driver_type"],
                    "definitionVersionId": resource["def_version_id"],
                    "definitionId": resource["def_id"],
                    "updatedAt": resource["updated_at"],
                    "graphResourceID": resource["gu_res_id"],
                },
                "relations": {
                    BLUEPRINT.ENVIRONMENT: f"{application['id']}/{environment['id']}",
                },
            }

        applications = await self.humanitec_client.get_all_applications()
        for application in applications:
            environments = await self.humanitec_client.get_all_environments(application)
            for environment in environments:
                resources = await self.humanitec_client.get_all_resources(
                    application, environment
                )
                resource_group = self.humanitec_client.group_resources_by_type(
                    resources
                )
                tasks = [
                    self.port_client.upsert_entity(
                        blueprint_id=BLUEPRINT.WORKLOAD,
                        entity_object=create_workload_entity(resource, application),
                    )
                    for resource in resource_group.get("modules", [])
                    if resource and resource["type"] == "workload"
                ]
                await asyncio.gather(*tasks)
        logger.info(f"Finished syncing entities for blueprint {BLUEPRINT.WORKLOAD}")

    async def sync_resource_graphs(self) -> None:
        logger.info(f"Syncing entities for blueprint {BLUEPRINT.RESOURCE_GRAPH}")

        def create_resource_graph_entity(
            graph_data, include_relations, application, environment
        ):
            print("GRAPH DATA", graph_data)
            entity = {
                "identifier": graph_data["guresid"],
                "title": self.remove_symbols_and_title_case(graph_data["def_id"]),
                "properties": {
                    "type": graph_data["type"],
                    "class": graph_data["class"],
                    "resourceSchema": graph_data["resource_schema"],
                    "resource": graph_data["resource"],
                },
                "relations": {},
            }
            if include_relations:

                entity["relations"] = {
                    BLUEPRINT.RESOURCE_GRAPH: graph_data["depends_on"],
                    BLUEPRINT.ENVIRONMENT: f"{application['id']}/{environment['id']}",
                }
            return entity

        applications = await self.humanitec_client.get_all_applications()
        for application in applications:
            environments = await self.humanitec_client.get_all_environments(application)
            for environment in environments:
                graph_nodes = await self.humanitec_client.get_dependency_graph(
                    application, environment
                )

                # First pass: Create entities without relations
                tasks = [
                    self.port_client.upsert_entity(
                        blueprint_id=BLUEPRINT.RESOURCE_GRAPH,
                        entity_object=create_resource_graph_entity(
                            node, False, application, environment
                        ),
                    )
                    for node in graph_nodes
                ]
                await asyncio.gather(*tasks)

                # Second pass: Update entities with relations
                tasks = [
                    self.port_client.upsert_entity(
                        blueprint_id=BLUEPRINT.RESOURCE_GRAPH,
                        entity_object=create_resource_graph_entity(
                            node, True, application, environment
                        ),
                    )
                    for node in graph_nodes
                ]
                await asyncio.gather(*tasks)
        logger.info(
            f"Finished syncing entities for blueprint {BLUEPRINT.RESOURCE_GRAPH}"
        )

    async def enrich_resource_with_graph(self, resource, application, environment):
        try:
            logger.info("Enriching resource %s with graph", resource["res_id"])
            data = {
                "id": resource["res_id"],
                "type": resource["type"],
                "resource": resource["resource"],
            }
            response = await self.humanitec_client.get_resource_graph(
                application, environment, [data]
            )

            resource.update(
                {"__resourceGraph": i for i in response if i["type"] == data["type"]}
            )
            return resource
        except Exception as e:
            logger.error(
                f"Failed to enrich resource {resource['res_id']} with graph: %s", str(e)
            )
            return resource

    async def sync_resources(self) -> None:
        logger.info(f"Syncing entities for blueprint {BLUEPRINT.RESOURCE}")

        def create_resource_entity(resource):
            print("RESOURCE", resource)
            workload_id = (
                resource["res_id"].split(".")[1]
                if resource["res_id"].split(".")[0].startswith("modules")
                else ""
            )
            resource_id = (
                f"{resource['app_id']}/{resource['env_id']}/{resource['res_id']}"
            )
            entity = {
                "identifier": resource_id,
                "title": self.remove_symbols_and_title_case(resource["def_id"]),
                "properties": {
                    "type": resource["type"],
                    "class": resource["class"],
                    "resource": resource["resource"],
                    "status": resource["status"],
                    "updateAt": resource["updated_at"],
                    "driverType": resource["driver_type"],
                },
                "relations": {},
            }
            if workload_id:
                workload_id = f"{resource['app_id']}/{resource['env_id']}/{workload_id}"
                entity["relations"][BLUEPRINT.WORKLOAD] = workload_id
                entity["relations"][BLUEPRINT.RESOURCE_GRAPH] = resource["gu_res_id"]
            return entity

        applications = await self.humanitec_client.get_all_applications()
        for application in applications:
            environments = await self.humanitec_client.get_all_environments(application)
            for environment in environments:
                resources = await self.humanitec_client.get_all_resources(
                    application, environment
                )

                entity_tasks = [
                    self.port_client.upsert_entity(
                        blueprint_id=BLUEPRINT.RESOURCE,
                        entity_object=create_resource_entity(resource),
                    )
                    for resource in resources
                ]
                await asyncio.gather(*entity_tasks)
                logger.info(
                    "Upserted resource entities for %s environment", environment["id"]
                )

        logger.info(f"Finished syncing entities for blueprint {BLUEPRINT.RESOURCE}")

    async def sync_all(self) -> None:
        await self.sync_applications()
        await self.sync_environments()
        await self.sync_workloads()
        await self.sync_resource_graphs()
        await self.sync_resources()
        logger.info("Event Finished")

    async def __call__(self, args) -> None:
        await self.sync_all()


if __name__ == "__main__":

    def validate_args(args):
        required_keys = ["org_id", "api_key", "port_client_id", "port_client_secret"]
        missing_keys = [key for key in required_keys if not getattr(args, key)]

        if missing_keys:
            logger.error(f"The following keys are required: {', '.join(missing_keys)}")
            return False
        return True

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--org-id",
        required=False,
        default=config("ORG_ID", ""),
        type=str,
        help="Humanitec organization ID",
    )
    parser.add_argument(
        "--api-key",
        required=False,
        default=config("API_KEY", ""),
        type=str,
        help="Humanitec API key",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=config("API_URL", "https://api.humanitec.com"),
        help="Humanitec API URL",
    )
    parser.add_argument(
        "--port-client-id",
        type=str,
        required=False,
        default=config("PORT_CLIENT_ID", ""),
        help="Port client ID",
    )
    parser.add_argument(
        "--port-client-secret",
        type=str,
        required=False,
        default=config("PORT_CLIENT_SECRET", ""),
        help="Port client secret",
    )
    args = parser.parse_args()
    if not (validate_args(args)):
        import sys

        sys.exit()

    exporter = HumanitecExporter(args)
    asyncio.run(exporter(args))
