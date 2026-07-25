"""
module3/templates/cdk_patterns.py
==================================
Reusable CDK patterns and templates for common AWS infrastructure.

These templates follow AWS best practices:
- Multi-AZ deployments for high availability
- Encryption at rest and in transit
- Least privilege IAM policies
- Security groups with minimal access
- CloudWatch monitoring and alarms
"""

from __future__ import annotations

import os
import re
from typing import Any


MAX_STRING_PARAM_LEN = int(os.getenv("MODULE3_TEMPLATE_MAX_STRING_PARAM_LEN", "256"))
MAX_ENV_VARS = int(os.getenv("MODULE3_TEMPLATE_MAX_ENV_VARS", "50"))
MAX_ENV_KEY_LEN = int(os.getenv("MODULE3_TEMPLATE_MAX_ENV_KEY_LEN", "64"))
MAX_ENV_VALUE_LEN = int(os.getenv("MODULE3_TEMPLATE_MAX_ENV_VALUE_LEN", "512"))

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")

_RDS_ENGINES = {"POSTGRES", "MYSQL", "MARIADB"}
_RDS_POSTGRES_VERSIONS = {"VER_13_12", "VER_14_9", "VER_15_4", "VER_16_1"}
_RDS_MYSQL_VERSIONS = {"VER_8_0_39", "VER_8_4_3"}
_RDS_MARIADB_VERSIONS = {"VER_10_6_14", "VER_10_11_6"}
_RDS_INSTANCE_CLASSES = {"BURSTABLE3", "BURSTABLE4_GRAVITON", "M6G", "M7G", "R6G", "R7G"}
_RDS_INSTANCE_SIZES = {"MICRO", "SMALL", "MEDIUM", "LARGE", "XLARGE", "X2LARGE"}
_RDS_REMOVAL_POLICIES = {"RETAIN", "SNAPSHOT", "DESTROY"}

_LAMBDA_RUNTIMES = {
    "PYTHON_3_11",
    "PYTHON_3_12",
    "NODEJS_18_X",
    "NODEJS_20_X",
    "JAVA_17",
    "JAVA_21",
}


def _validate_int(name: str, value: int, min_value: int, max_value: int) -> int:
    """Validate bounded integer input."""
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < min_value or value > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return value


def _validate_bool(name: str, value: bool) -> bool:
    """Validate boolean input."""
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _validate_safe_string(name: str, value: str, *, allow_empty: bool = False) -> str:
    """Validate bounded string that can be safely embedded in generated code."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    trimmed = value.strip()
    if not allow_empty and not trimmed:
        raise ValueError(f"{name} must not be empty")
    if len(trimmed) > MAX_STRING_PARAM_LEN:
        raise ValueError(f"{name} exceeds maximum length ({MAX_STRING_PARAM_LEN})")
    if trimmed and not _SAFE_NAME_RE.match(trimmed):
        raise ValueError(f"{name} contains unsupported characters")
    return trimmed


def _validate_environment(environment: dict[str, str] | None) -> dict[str, str]:
    """Validate Lambda/ECS environment map."""
    if environment is None:
        return {}
    if not isinstance(environment, dict):
        raise TypeError("environment must be a dictionary of string keys and values")
    if len(environment) > MAX_ENV_VARS:
        raise ValueError(f"environment exceeds maximum entries ({MAX_ENV_VARS})")

    validated: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("environment keys must be non-empty strings")
        if not isinstance(value, str):
            raise ValueError("environment values must be strings")
        normalized_key = key.strip()
        if len(normalized_key) > MAX_ENV_KEY_LEN:
            raise ValueError(f"environment key '{normalized_key}' exceeds max length ({MAX_ENV_KEY_LEN})")
        if len(value) > MAX_ENV_VALUE_LEN:
            raise ValueError(f"environment value for key '{normalized_key}' exceeds max length ({MAX_ENV_VALUE_LEN})")
        validated[normalized_key] = value
    return validated


def _validate_enum(name: str, value: str, allowed: set[str]) -> str:
    """Validate enum-like string values."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip().upper()
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _validate_rds_version(engine: str, version: str) -> str:
    """Validate version belongs to selected RDS engine."""
    if not isinstance(version, str):
        raise TypeError("version must be a string")
    normalized = version.strip().upper()
    allowed_by_engine = {
        "POSTGRES": _RDS_POSTGRES_VERSIONS,
        "MYSQL": _RDS_MYSQL_VERSIONS,
        "MARIADB": _RDS_MARIADB_VERSIONS,
    }
    allowed = allowed_by_engine[engine]
    if normalized not in allowed:
        raise ValueError(f"version for engine {engine} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _py_literal(value: Any) -> str:
    """Render safe Python literal for template insertion."""
    return repr(value)


# CDK pattern templates as strings that can be customized
CDK_PATTERNS = {
    "vpc": """
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
)
from constructs import Construct


class VpcStack(Stack):
    \"\"\"VPC stack with public and private subnets across multiple AZs.\"\"\"

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC with public and private subnets
        self.vpc = ec2.Vpc(
            self,
            "VPC",
            max_azs={max_azs},
            nat_gateways={nat_gateways},
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
""",
    "ecs": """
from aws_cdk import (
    Stack,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs,
    Duration,
)
from constructs import Construct


class EcsStack(Stack):
    \"\"\"ECS Fargate service with Application Load Balancer.\"\"\"

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            container_insights=True,
        )

        # Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            memory_limit_mib={memory},
            cpu={cpu},
        )

        # Container
        container = task_definition.add_container(
            {container_name},
            image=ecs.ContainerImage.from_registry({image}),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix={service_name},
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            environment={environment},
        )

        container.add_port_mappings(
            ecs.PortMapping(container_port={container_port})
        )

        # Fargate Service
        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            desired_count={desired_count},
            assign_public_ip=False,
        )

        # Application Load Balancer
        lb = elbv2.ApplicationLoadBalancer(
            self,
            "ALB",
            vpc=vpc,
            internet_facing=True,
        )

        listener = lb.add_listener(
            "Listener",
            port=80,
        )

        listener.add_targets(
            "ECS",
            port={container_port},
            targets=[service],
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
            ),
        )
""",
    "rds": """
from aws_cdk import (
    Stack,
    aws_rds as rds,
    aws_ec2 as ec2,
    RemovalPolicy,
    Duration,
)
from constructs import Construct


class RdsStack(Stack):
    \"\"\"RDS database with Multi-AZ deployment and encryption.\"\"\"

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Security Group
        db_security_group = ec2.SecurityGroup(
            self,
            "DBSecurityGroup",
            vpc=vpc,
            description="Security group for RDS database",
            allow_all_outbound=False,
        )

        # RDS Instance
        self.database = rds.DatabaseInstance(
            self,
            "Database",
            engine=rds.DatabaseInstanceEngine.{engine}(
                version=rds.{engine}EngineVersion.{version}
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.{instance_class},
                ec2.InstanceSize.{instance_size},
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[db_security_group],
            multi_az={multi_az},
            allocated_storage={allocated_storage},
            storage_encrypted=True,
            backup_retention=Duration.days({backup_retention}),
            deletion_protection={deletion_protection},
            removal_policy=RemovalPolicy.{removal_policy},
        )
""",
    "elasticache": """
from aws_cdk import (
    Stack,
    aws_elasticache as elasticache,
    aws_ec2 as ec2,
)
from constructs import Construct


class ElastiCacheStack(Stack):
    \"\"\"ElastiCache Redis cluster with encryption.\"\"\"

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Security Group
        cache_security_group = ec2.SecurityGroup(
            self,
            "CacheSecurityGroup",
            vpc=vpc,
            description="Security group for ElastiCache",
        )

        # Subnet Group
        subnet_group = elasticache.CfnSubnetGroup(
            self,
            "SubnetGroup",
            description="Subnet group for ElastiCache",
            subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
        )

        # Replication Group (Redis Cluster)
        self.cache_cluster = elasticache.CfnReplicationGroup(
            self,
            "RedisCluster",
            replication_group_description={description},
            engine="redis",
            engine_version={engine_version},
            cache_node_type={node_type},
            num_cache_clusters={num_nodes},
            automatic_failover_enabled={automatic_failover},
            multi_az_enabled={multi_az},
            at_rest_encryption_enabled=True,
            transit_encryption_enabled=True,
            cache_subnet_group_name=subnet_group.ref,
            security_group_ids=[cache_security_group.security_group_id],
        )
""",
    "s3": """
from aws_cdk import (
    Stack,
    aws_s3 as s3,
    RemovalPolicy,
)
from constructs import Construct


class S3Stack(Stack):
    \"\"\"S3 bucket with encryption and versioning.\"\"\"

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket
        self.bucket = s3.Bucket(
            self,
            "Bucket",
            bucket_name={bucket_name},
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned={versioned},
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.{removal_policy},
            auto_delete_objects={auto_delete},
        )
""",
    "lambda": """
from aws_cdk import (
    Stack,
    aws_lambda as lambda_,
    aws_ec2 as ec2,
    aws_logs as logs,
    Duration,
)
from constructs import Construct


class LambdaStack(Stack):
    \"\"\"Lambda function with VPC access and monitoring.\"\"\"

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Lambda Function
        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.{runtime},
            handler={handler},
            code=lambda_.Code.from_asset({code_path}),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            memory_size={memory_size},
            timeout=Duration.seconds({timeout}),
            environment={environment},
            log_retention=logs.RetentionDays.ONE_WEEK,
        )
""",
}


def generate_vpc_stack(
    max_azs: int = 2,
    nat_gateways: int = 1,
) -> str:
    """Generate a VPC stack with customizable parameters."""
    max_azs = _validate_int("max_azs", max_azs, 2, 6)
    nat_gateways = _validate_int("nat_gateways", nat_gateways, 0, 4)
    return CDK_PATTERNS["vpc"].format(
        max_azs=max_azs,
        nat_gateways=nat_gateways,
    )


def generate_ecs_stack(
    service_name: str = "app",
    container_name: str = "app-container",
    image: str = "nginx:latest",
    container_port: int = 80,
    memory: int = 512,
    cpu: int = 256,
    desired_count: int = 2,
    environment: dict[str, str] | None = None,
) -> str:
    """Generate an ECS Fargate stack with ALB."""
    service_name = _validate_safe_string("service_name", service_name)
    container_name = _validate_safe_string("container_name", container_name)
    image = _validate_safe_string("image", image)
    container_port = _validate_int("container_port", container_port, 1, 65535)
    memory = _validate_int("memory", memory, 128, 30720)
    cpu = _validate_int("cpu", cpu, 128, 16384)
    desired_count = _validate_int("desired_count", desired_count, 1, 100)
    env_dict = _validate_environment(environment)
    return CDK_PATTERNS["ecs"].format(
        service_name=_py_literal(service_name),
        container_name=_py_literal(container_name),
        image=_py_literal(image),
        container_port=container_port,
        memory=memory,
        cpu=cpu,
        desired_count=desired_count,
        environment=_py_literal(env_dict),
    )


def generate_rds_stack(
    engine: str = "POSTGRES",
    version: str = "VER_15_4",
    instance_class: str = "BURSTABLE3",
    instance_size: str = "SMALL",
    multi_az: bool = True,
    allocated_storage: int = 20,
    backup_retention: int = 7,
    deletion_protection: bool = True,
    removal_policy: str = "SNAPSHOT",
) -> str:
    """Generate an RDS database stack."""
    engine = _validate_enum("engine", engine, _RDS_ENGINES)
    version = _validate_rds_version(engine, version)
    instance_class = _validate_enum("instance_class", instance_class, _RDS_INSTANCE_CLASSES)
    instance_size = _validate_enum("instance_size", instance_size, _RDS_INSTANCE_SIZES)
    multi_az = _validate_bool("multi_az", multi_az)
    allocated_storage = _validate_int("allocated_storage", allocated_storage, 20, 65536)
    backup_retention = _validate_int("backup_retention", backup_retention, 1, 35)
    deletion_protection = _validate_bool("deletion_protection", deletion_protection)
    removal_policy = _validate_enum("removal_policy", removal_policy, _RDS_REMOVAL_POLICIES)

    return CDK_PATTERNS["rds"].format(
        engine=engine,
        version=version,
        instance_class=instance_class,
        instance_size=instance_size,
        multi_az=multi_az,
        allocated_storage=allocated_storage,
        backup_retention=backup_retention,
        deletion_protection=deletion_protection,
        removal_policy=removal_policy,
    )


def generate_elasticache_stack(
    description: str = "Redis cache cluster",
    engine_version: str = "7.0",
    node_type: str = "cache.t3.micro",
    num_nodes: int = 2,
    automatic_failover: bool = True,
    multi_az: bool = True,
) -> str:
    """Generate an ElastiCache Redis stack."""
    description = _validate_safe_string("description", description)
    engine_version = _validate_safe_string("engine_version", engine_version)
    node_type = _validate_safe_string("node_type", node_type)
    num_nodes = _validate_int("num_nodes", num_nodes, 1, 6)
    automatic_failover = _validate_bool("automatic_failover", automatic_failover)
    multi_az = _validate_bool("multi_az", multi_az)

    return CDK_PATTERNS["elasticache"].format(
        description=_py_literal(description),
        engine_version=_py_literal(engine_version),
        node_type=_py_literal(node_type),
        num_nodes=num_nodes,
        automatic_failover=automatic_failover,
        multi_az=multi_az,
    )


def generate_s3_stack(
    bucket_name: str | None = None,
    versioned: bool = True,
    removal_policy: str = "RETAIN",
    auto_delete: bool = False,
) -> str:
    """Generate an S3 bucket stack."""
    if bucket_name is not None:
        bucket_name = _validate_safe_string("bucket_name", bucket_name, allow_empty=False)
    versioned = _validate_bool("versioned", versioned)
    removal_policy = _validate_enum("removal_policy", removal_policy, _RDS_REMOVAL_POLICIES)
    auto_delete = _validate_bool("auto_delete", auto_delete)

    return CDK_PATTERNS["s3"].format(
        bucket_name=_py_literal(bucket_name or ""),
        versioned=versioned,
        removal_policy=removal_policy,
        auto_delete=auto_delete,
    )


def generate_lambda_stack(
    runtime: str = "PYTHON_3_11",
    handler: str = "index.handler",
    code_path: str = "lambda",
    memory_size: int = 128,
    timeout: int = 30,
    environment: dict[str, str] | None = None,
) -> str:
    """Generate a Lambda function stack."""
    runtime = _validate_enum("runtime", runtime, _LAMBDA_RUNTIMES)
    handler = _validate_safe_string("handler", handler)
    code_path = _validate_safe_string("code_path", code_path)
    memory_size = _validate_int("memory_size", memory_size, 128, 10240)
    timeout = _validate_int("timeout", timeout, 1, 900)
    env_dict = _validate_environment(environment)

    return CDK_PATTERNS["lambda"].format(
        runtime=runtime,
        handler=_py_literal(handler),
        code_path=_py_literal(code_path),
        memory_size=memory_size,
        timeout=timeout,
        environment=_py_literal(env_dict),
    )
