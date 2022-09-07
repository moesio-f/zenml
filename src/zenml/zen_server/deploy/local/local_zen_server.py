#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.
"""Local ZenML server deployment service implementation."""

import ipaddress
import os
import shutil
from typing import Dict, List, Optional, Tuple, Union, cast

from zenml.config.global_config import GlobalConfiguration
from zenml.constants import (
    DEFAULT_LOCAL_SERVICE_IP_ADDRESS,
    ENV_ZENML_CONFIG_PATH,
    ZEN_SERVER_ENTRYPOINT,
)
from zenml.enums import StoreType
from zenml.logger import get_logger
from zenml.services import (
    LocalDaemonService,
    LocalDaemonServiceConfig,
    LocalDaemonServiceEndpoint,
    ServiceType,
)
from zenml.utils.io_utils import get_global_config_directory
from zenml.zen_server.deploy.deployment import ServerDeploymentConfig

logger = get_logger(__name__)

ZEN_SERVER_HEALTHCHECK_URL_PATH = "health"

LOCAL_ZENML_SERVER_CONFIG_PATH = os.path.join(
    get_global_config_directory(),
    "zen_server",
    "local",
)
LOCAL_ZENML_SERVER_CONFIG_FILENAME = os.path.join(
    LOCAL_ZENML_SERVER_CONFIG_PATH, "service.json"
)
LOCAL_ZENML_SERVER_GLOBAL_CONFIG_PATH = os.path.join(
    LOCAL_ZENML_SERVER_CONFIG_PATH, ".zenconfig"
)

LOCAL_ZENML_SERVER_DEFAULT_TIMEOUT = 30


class LocalServerDeploymentConfig(ServerDeploymentConfig):
    """Local server deployment configuration.

    Attributes:
        port: The TCP port number where the server is accepting connections.
        address: The IP address where the server is reachable.
    """

    port: int = 8237
    address: Union[
        ipaddress.IPv4Address, ipaddress.IPv6Address
    ] = ipaddress.IPv4Address(DEFAULT_LOCAL_SERVICE_IP_ADDRESS)


class LocalZenServerConfig(LocalDaemonServiceConfig):
    """Local Zen server configuration.

    Attributes:
        server: The deployment configuration.
    """

    server: LocalServerDeploymentConfig


class LocalZenServer(LocalDaemonService):
    """Service daemon that can be used to start a local ZenServer.

    Attributes:
        config: service configuration
        endpoint: optional service endpoint
    """

    SERVICE_TYPE = ServiceType(
        name="local_zenml_server",
        type="zen_server",
        flavor="local",
        description="Local ZenML server deployment",
    )

    config: LocalZenServerConfig
    endpoint: LocalDaemonServiceEndpoint

    def _copy_global_configuration(self) -> None:
        """Copy the global configuration to the local ZenML server location.

        The local ZenML server global configuration is a copy of the local
        global configuration with the store configuration set to point to the
        local store.
        """
        gc = GlobalConfiguration()

        # this creates a copy of the global configuration with the store
        # set to the local store and saves it to the server configuration path
        gc.copy_configuration(
            config_path=LOCAL_ZENML_SERVER_GLOBAL_CONFIG_PATH,
            store_config=gc.get_default_store(),
        )

    @classmethod
    def get_service(cls) -> Optional["LocalZenServer"]:
        """Load and return the local ZenML server service, if present.

        Returns:
            The local ZenML server service or None, if the local server
            deployment is not found.
        """
        from zenml.services import ServiceRegistry

        try:
            with open(LOCAL_ZENML_SERVER_CONFIG_FILENAME, "r") as f:
                return cast(
                    LocalZenServer,
                    ServiceRegistry().load_service_from_json(f.read()),
                )
        except FileNotFoundError:
            return None

    def _get_daemon_cmd(self) -> Tuple[List[str], Dict[str, str]]:
        """Get the command to start the daemon.

        Overrides the base class implementation to add the environment variable
        that forces the ZenML server to use the copied global config.

        Returns:
            The command to start the daemon and the environment variables to
            set for the command.
        """
        cmd, env = super()._get_daemon_cmd()
        env[ENV_ZENML_CONFIG_PATH] = LOCAL_ZENML_SERVER_GLOBAL_CONFIG_PATH
        return cmd, env

    def provision(self) -> None:
        """Provision the service."""
        self._copy_global_configuration()
        super().provision()

    def deprovision(self, force: bool = False) -> None:
        """Deprovision the service.

        Args:
            force: if True, the service daemon will be forcefully stopped
        """
        super().deprovision(force=force)
        shutil.rmtree(LOCAL_ZENML_SERVER_CONFIG_PATH)

    def run(self) -> None:
        """Run the ZenServer.

        Raises:
            ValueError: if started with a global configuration that connects to
                another ZenML server.
        """
        import uvicorn  # type: ignore[import]

        gc = GlobalConfiguration()
        if gc.store and gc.store.type == StoreType.REST:
            raise ValueError(
                "The ZenML server cannot be started with REST store type."
            )
        logger.info(
            "Starting ZenServer as blocking "
            "process... press CTRL+C once to stop it."
        )

        self.endpoint.prepare_for_start()

        try:
            uvicorn.run(
                ZEN_SERVER_ENTRYPOINT,
                host=self.endpoint.config.ip_address,
                port=self.endpoint.config.port,
                log_level="info",
            )
        except KeyboardInterrupt:
            logger.info("ZenServer stopped. Resuming normal execution.")
