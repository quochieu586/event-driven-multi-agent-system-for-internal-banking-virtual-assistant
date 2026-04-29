from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    RemovalPolicy,
    Duration,
)
from constructs import Construct

LLM_ARN = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"

class CodeStack(Stack):
    table: dynamodb.Table
    s3_bucket: s3.Bucket
    supervisor_agent: _lambda.Function
    customer_agent: _lambda.Function
    it_agent: _lambda.Function
    finance_agent: _lambda.Function
    dynamodb_handler: _lambda.Function

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Create a DynamoDB Table (The Database)
        self._init_dynamodb_table()

        # 2. Create s3 bucket for knowledge base (The Library)
        self._create_s3_bucket()

        # 3. Create the Supervisor Agent (The Librarian)
        self._init_supervisor_agent()

        # 4. Create the Specialist Agent (Customer, IT, Finance)
        self._init_customer_agent()
        self._init_it_agent()
        self._init_finance_agent()

        # 5. Grant permissions for Supervisor to invoke the specialist agents
        self.customer_agent.grant_invoke(self.supervisor_agent)
        self.it_agent.grant_invoke(self.supervisor_agent)
        self.finance_agent.grant_invoke(self.supervisor_agent)

        # 6. Pass the ARNs to the Supervisor's environment
        self.supervisor_agent.add_environment("CUSTOMER_AGENT_ARN", self.customer_agent.function_arn)
        self.supervisor_agent.add_environment("IT_AGENT_ARN", self.it_agent.function_arn)
        self.supervisor_agent.add_environment("FINANCE_AGENT_ARN", self.finance_agent.function_arn)

    def _init_dynamodb_table(self):
        """
        Create a DynamoDB table (Database)
        """
        self.table = dynamodb.Table(
            self, "BankingLogs",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            time_to_live_attribute="ttl",           # Automatically delete items after a certain time
            removal_policy=RemovalPolicy.DESTROY    # Deletes data when we delete stack.
        )

        self.dynamodb_handler = _lambda.Function(
            self, "DynamoDBHandlerFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="dynamodb_handler.handler",
            code=_lambda.Code.from_asset("agents"),
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                'TABLE_NAME': self.table.table_name
            }
        )

        # grant read/write permissions to the DynamoDB handler so it can be used by the agents
        self.table.grant_read_write_data(self.dynamodb_handler)

        # create a public URL for the Lambda function (API Gateway)
        self.dynamodb_handler.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE # Publicly accessible for testing
        )
    
    def _create_s3_bucket(self):
        """
        Create an S3 bucket to serve as the Knowledge Base (Library)
        """
        kb_bucket = s3.Bucket(
            self, "BankingDocsBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        return kb_bucket
    
    def _init_supervisor_agent(self):
        """
        Create the Supervisor Agent as a Lambda function (Librarian).
        """
        self.supervisor_agent = _lambda.Function(
            self, "SupervisorAgentFunction",
            runtime=_lambda.Runtime.PYTHON_3_11, # M2 Mac logic: AWS handles the architecture
            handler="supervisor.handler",         # filename.function_name
            code=_lambda.Code.from_asset("agents"), # Points to your local folder
            timeout=Duration.seconds(20), # Adjust as needed
            memory_size=512, # Adjust as needed
            environment={
                'TABLE_NAME': self.table.table_name, # Pass the table name to the Lambda function
                'LLM_ARN': LLM_ARN, # Pass the LLM ARN
                'CUSTOMER_SQS_URL': 'https://sqs.us-east-1.amazonaws.com/725301416092/CustomerQueue',
                'IT_SQS_URL': 'https://sqs.us-east-1.amazonaws.com/725301416092/ITQueue',
                'FINANCE_SQS_URL': 'https://sqs.us-east-1.amazonaws.com/725301416092/FinanceQueue'
            }
        )
        
        # give the lambda permissions to read/write to the DynamoDB table
        self.table.grant_read_write_data(self.supervisor_agent)

        # give the lambda permissions to talk to Bedrock
        self.supervisor_agent.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
            resources=["*"] # In production, restrict this to your specific KB ARN
        ))

        # give the lambda permissions to invoke the specific Bedrock model it will use
        self.supervisor_agent.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[LLM_ARN]
        ))
        
        # create a public URL for the Lambda function (API Gateway)
        self.supervisor_agent.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE # Publicly accessible for testing
        )

        # # Output the URL so we can easily find it after deployment
        # CfnOutput(self, "AgentUrl", value=self.supervisor_agent.url)

    def _init_customer_agent(self):
        """
        Create the Customer Agent as a Lambda function.
        """
        self.customer_agent = _lambda.Function(
            self, "CustomerAgentFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="customer.handler",
            code=_lambda.Code.from_asset("agents"),
            timeout=Duration.seconds(10), # Adjust as needed
            memory_size=256, # Adjust as needed
            environment={
                'KNOWLEDGE_BASE_ID': 'PZV13QDRMV', # Pass the Knowledge Base ID to the Lambda function
                'LLM_ARN': LLM_ARN, # Pass the LLM ARN
                'GOOGLE_APPLICATION_CREDENTIALS': 'wif_credentials.json', # Path to the WIF credentials file in the Lambda environment
                'TABLE_NAME': self.table.table_name, # Pass the table name to the Lambda function
                'SPREADSHEET_ID': '1ONF1oTXfPhY3JXVRbmvi934d2eOXeZQq3cjbx7-Rw-I' # Pass the Spreadsheet ID for Google Sheets interactions
            }
        )

        self.table.grant_read_write_data(self.customer_agent)

        # give the lambda permissions to talk to Bedrock
        self.customer_agent.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
            resources=["*"] # In production, restrict this to your specific KB ARN
        ))

        # give the lambda permissions to invoke the specific Bedrock model it will use
        self.customer_agent.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[
                LLM_ARN
            ]
        ))

        # create a public URL for the Lambda function (API Gateway)
        self.customer_agent.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE # Publicly accessible for testing
        )
    
    def _init_it_agent(self):
        """
        Create the IT Agent as a Lambda function.
        """
        self.it_agent = _lambda.Function(
            self, "ITAgentFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="it.handler",
            code=_lambda.Code.from_asset("agents"),
            timeout=Duration.seconds(10), # Adjust as needed
            memory_size=256, # Adjust as needed
            environment={
                'KNOWLEDGE_BASE_ID': 'D8QZ41O0YM', # Pass the Knowledge Base ID to the Lambda function
                'LLM_ARN': LLM_ARN, # Pass the LLM ARN
                'TABLE_NAME': self.table.table_name, # Pass the table name to the Lambda function
            }
        )

        self.table.grant_read_write_data(self.it_agent)

        # give the lambda permissions to talk to Bedrock
        self.it_agent.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
            resources=["*"] # In production, restrict this to your specific KB ARN
        ))

        # give the lambda permissions to invoke the specific Bedrock model it will use
        self.it_agent.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[
                LLM_ARN
            ]
        ))

        # create a public URL for the Lambda function (API Gateway)
        self.it_agent.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE # Publicly accessible for testing
        )
    
    def _init_finance_agent(self):
        """
        Create the Finance Agent as a Lambda function.
        """
        self.finance_agent = _lambda.Function(
            self, "FinanceAgentFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="finance.handler",
            code=_lambda.Code.from_asset("agents"),
            timeout=Duration.seconds(10), # Adjust as needed
            memory_size=256, # Adjust as needed
            environment={
                'KNOWLEDGE_BASE_ID': '02HODSHBCN',      # Pass the Knowledge Base ID to the Lambda function
                'LLM_ARN': LLM_ARN,                     # Pass the LLM ARN
                'TABLE_NAME': self.table.table_name,    # Pass the table name to the Lambda function
            }
        )

        self.table.grant_read_write_data(self.finance_agent)

        # give the lambda permissions to talk to Bedrock
        self.finance_agent.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
            resources=["*"] # In production, restrict this to your specific KB ARN
        ))

        # give the lambda permissions to invoke the specific Bedrock model it will use
        self.finance_agent.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[LLM_ARN]
        ))

        # create a public URL for the Lambda function (API Gateway)
        self.finance_agent.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE # Publicly accessible for testing
        )
