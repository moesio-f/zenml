#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.
"""Implementation of the BentoML model deployer pipeline step."""

from typing import List, Optional, cast

import bentoml
from bentoml._internal.bento import bento

from zenml import get_step_context, step
from zenml.integrations.bentoml.model_deployers.bentoml_model_deployer import (
    BentoMLModelDeployer,
)
from zenml.integrations.bentoml.services.bentoml_deployment import (
    BentoMLDeploymentConfig,
    BentoMLDeploymentService,
    SSLBentoMLParametersConfig,
)
from zenml.logger import get_logger
from zenml.utils import source_utils

logger = get_logger(__name__)


@step(enable_cache=True)
def bentoml_model_deployer_step(
    bento: bento.Bento,
    model_name: str,
    port: int,
    deploy_decision: bool = True,
    workers: Optional[int] = 1,
    backlog: Optional[int] = 2048,
    production: bool = False,
    working_dir: Optional[str] = None,
    host: Optional[str] = None,
    ssl_certfile: Optional[str] = None,
    ssl_keyfile: Optional[str] = None,
    ssl_keyfile_password: Optional[str] = None,
    ssl_version: Optional[str] = None,
    ssl_cert_reqs: Optional[str] = None,
    ssl_ca_certs: Optional[str] = None,
    ssl_ciphers: Optional[str] = None,
    timeout: int = 30,
) -> BentoMLDeploymentService:
    """Model deployer pipeline step for BentoML.

    This step deploys a given Bento to a local BentoML http prediction server.

    Args:
        bento: the bento artifact to deploy
        model_name: the name of the model to deploy.
        port: the port to use for the prediction service.
        deploy_decision: whether to deploy the model or not
        workers: number of workers to use for the prediction service
        backlog: the number of requests to queue up before rejecting requests.
        production: whether to deploy the service in production mode.
        working_dir: the working directory to use for the prediction service.
        host: the host to use for the prediction service.
        ssl_certfile: the path to the ssl cert file.
        ssl_keyfile: the path to the ssl key file.
        ssl_keyfile_password: the password for the ssl key file.
        ssl_version: the ssl version to use.
        ssl_cert_reqs: the ssl cert requirements.
        ssl_ca_certs: the path to the ssl ca certs.
        ssl_ciphers: the ssl ciphers to use.
        timeout: the number of seconds to wait for the service to start/stop.

    Returns:
        BentoML deployment service
    """
    # get the current active model deployer
    model_deployer = cast(
        BentoMLModelDeployer, BentoMLModelDeployer.get_active_model_deployer()
    )

    # get pipeline name, step name and run id
    step_context = get_step_context()
    pipeline_name = step_context.pipeline.name
    run_name = step_context.pipeline_run.name
    step_name = step_context.step_run.name

    # fetch existing services with same pipeline name, step name and model name
    existing_services = model_deployer.find_model_server(
        pipeline_name=pipeline_name,
        run_name=run_name,
        pipeline_step_name=step_name,
        model_name=model_name,
        service_type=BentoMLDeploymentService.SERVICE_TYPE,
    )

    # Return the apis endpoint of the defined service to use in the predict.
    # This is a workaround to get the endpoints of the service defined as functions
    # from the user code in the BentoML service.
    def service_apis(bento_tag: str) -> List[str]:
        # Add working dir in the bentoml load
        service = bentoml.load(
            bento_identifier=bento_tag,
            working_dir=working_dir or source_utils.get_source_root(),
        )
        apis = service.apis
        apis_paths = list(apis.keys())
        return apis_paths

    # create a config for the new model service
    predictor_cfg = BentoMLDeploymentConfig(
        model_name=model_name,
        bento=str(bento.tag),
        model_uri=bento.info.labels.get("model_uri"),
        bento_uri=bento.info.labels.get("bento_uri"),
        apis=service_apis(str(bento.tag)),
        workers=workers,
        host=host,
        backlog=backlog,
        working_dir=working_dir or source_utils.get_source_root(),
        port=port,
        pipeline_name=pipeline_name,
        run_name=run_name,
        pipeline_step_name=step_name,
        ssl_parameters=SSLBentoMLParametersConfig(
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            ssl_keyfile_password=ssl_keyfile_password,
            ssl_version=ssl_version,
            ssl_cert_reqs=ssl_cert_reqs,
            ssl_ca_certs=ssl_ca_certs,
            ssl_ciphers=ssl_ciphers,
        ),
    )

    # Creating a new service with inactive state and status by default
    if existing_services:
        service = cast(BentoMLDeploymentService, existing_services[0])

    if not deploy_decision and existing_services:
        logger.info(
            f"Skipping model deployment because the model quality does not "
            f"meet the criteria. Reusing last model server deployed by step "
            f"'{step_name}' and pipeline '{pipeline_name}' for model "
            f"'{model_name}'..."
        )
        if not service.is_running:
            service.start(timeout=timeout)
        return service

    # create a new model deployment and replace an old one if it exists
    new_service = cast(
        BentoMLDeploymentService,
        model_deployer.deploy_model(
            replace=True,
            config=predictor_cfg,
            timeout=timeout,
            service_type=BentoMLDeploymentService.SERVICE_TYPE,
        ),
    )

    logger.info(
        f"BentoML deployment service started and reachable at:\n"
        f"    {new_service.prediction_url}\n"
    )

    return new_service
