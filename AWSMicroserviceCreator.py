import boto3
import json
import time
import requests
import subprocess
from typing import Dict, List, Optional
from dataclasses import dataclass

@dataclass
class MicroserviceConfig:
    service_name: str
    region: str
    runtime: str = 'python3.12'
    memory_size: int = 1024
    timeout: int = 30
    provisioned_concurrency: int = 5
    dynamo_read_capacity: int = 5
    dynamo_write_capacity: int = 5
    github_token: str  # GitHub token to create repositories and manage actions
    github_username: str  # GitHub username

class AWSMicroserviceCreator:
    def __init__(self, config: MicroserviceConfig):
        self.config = config
        self.lambda_client = boto3.client('lambda', region_name=config.region)
        self.apigateway_client = boto3.client('apigateway', region_name=config.region)
        self.dynamodb_client = boto3.client('dynamodb', region_name=config.region)
        self.iam_client = boto3.client('iam', region_name=config.region)

    def create_microservice(self) -> Dict:
        """Create all required AWS resources for the microservice and GitHub repo"""
        print(f"Creating microservice: {self.config.service_name}")
        
        # Create GitHub repository and sync code
        repo_details = self.create_github_repo_and_push_code()
        
        # Create resources
        role_arn = self.create_lambda_role()
        table_name = self.create_dynamodb_table()
        function_arn = self.create_lambda_function(role_arn, table_name)
        api_id = self.create_api_gateway(function_arn)
        
        # Return AWS resources and GitHub details
        return {
            'role_arn': role_arn,
            'table_name': table_name,
            'function_arn': function_arn,
            'api_id': api_id,
            'github_repo_url': repo_details['clone_url']
        }

    def create_github_repo_and_push_code(self) -> Dict:
        """Create a GitHub repository and push code to it"""
        repo_name = f"{self.config.service_name}-repo"
        
        # Create GitHub repo using GitHub API
        repo_url = self.create_github_repo(repo_name)
        
        # Push microservice code to the GitHub repo
        self.push_code_to_github(repo_url)
        
        # Create GitHub Actions workflow for auto-deployment
        self.create_github_actions_workflow(repo_name)
        
        return {'clone_url': repo_url}

    def create_github_repo(self, repo_name: str) -> str:
        """Create a GitHub repository using the GitHub API"""
        url = 'https://api.github.com/user/repos'
        headers = {
            'Authorization': f'token {self.config.github_token}'
        }
        
        data = {
            "name": repo_name,
            "private": False,
            "auto_init": True,
            "gitignore_template": "Python"
        }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 201:
            print(f"Successfully created GitHub repository: {repo_name}")
            return response.json()['clone_url']
        else:
            print(f"Failed to create GitHub repo: {response.status_code}, {response.text}")
            return ""

    def push_code_to_github(self, repo_url: str):
        """Push the microservice code to GitHub"""
        # Assuming that the code is in the current working directory
        local_directory = '.'  # You can specify the path to the microservice code
        
        subprocess.run(["git", "init"], cwd=local_directory)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=local_directory)
        subprocess.run(["git", "add", "."], cwd=local_directory)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=local_directory)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=local_directory)
        
        print(f"Successfully pushed code to {repo_url}")

    def create_github_actions_workflow(self, repo_name: str):
        """Create a GitHub Actions workflow for Lambda auto-deployment"""
        url = f'https://api.github.com/repos/{self.config.github_username}/{repo_name}/contents/.github/workflows/deploy.yml'
        headers = {
            'Authorization': f'token {self.config.github_token}'
        }
        
        workflow_content = """
name: Deploy to AWS Lambda

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout Code
      uses: actions/checkout@v2

    - name: Set up AWS CLI
      uses: aws-actions/configure-aws-credentials@v1
      with:
        aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
        aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        aws-region: ${{ secrets.AWS_REGION }}

    - name: Create a deployment package
      run: |
        zip -r function.zip .  # Create a ZIP of your code

    - name: Deploy to AWS Lambda
      run: |
        aws lambda update-function-code --function-name ${{ secrets.LAMBDA_FUNCTION_NAME }} --zip-file fileb://function.zip

    - name: Clean up
      run: |
        rm -rf function.zip
"""
        
        data = {
            "message": "Add GitHub Actions workflow for AWS Lambda deployment",
            "content": workflow_content.encode("utf-8").decode("utf-8")
        }
        
        response = requests.put(url, headers=headers, json=data)
        
        if response.status_code == 201:
            print("Successfully created GitHub Actions workflow.")
        else:
            print(f"Failed to create workflow: {response.status_code}, {response.text}")

    def create_lambda_role(self) -> str:
        """Create IAM role for Lambda with necessary permissions"""
        role_name = f"{self.config.service_name}-lambda-role"
        
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        
        try:
            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy)
            )
            role_arn = response['Role']['Arn']
            
            # Attach necessary policies
            policies = [
                'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
                'arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess'
            ]
            
            for policy in policies:
                self.iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy
                )
            
            # Wait for role to propagate
            time.sleep(10)
            return role_arn
            
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            return self.iam_client.get_role(RoleName=role_name)['Role']['Arn']

    def create_dynamodb_table(self) -> str:
        """Create DynamoDB table with specified configuration"""
        table_name = f"{self.config.service_name}-table"
        
        try:
            response = self.dynamodb_client.create_table(
                TableName=table_name,
                KeySchema=[
                    {'AttributeName': 'id', 'KeyType': 'HASH'}
                ],
                AttributeDefinitions=[
                    {'AttributeName': 'id', 'AttributeType': 'S'}
                ],
                ProvisionedThroughput={
                    'ReadCapacityUnits': self.config.dynamo_read_capacity,
                    'WriteCapacityUnits': self.config.dynamo_write_capacity
                }
            )
            
            # Wait for table to be created
            self.dynamodb_client.get_waiter('table_exists').wait(
                TableName=table_name
            )
            
            return table_name
            
        except self.dynamodb_client.exceptions.ResourceInUseException:
            return table_name

    def create_lambda_function(self, role_arn: str, table_name: str) -> str:
        """Create Lambda function with provisioned concurrency"""
        function_name = f"{self.config.service_name}-function"
        
        # Basic Lambda handler code
        handler_code = """
import json
import boto3
from typing import Dict, Any

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('{}')

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    http_method = event['httpMethod']
    path = event['path']
    
    if http_method == 'GET':
        if path == '/items':
            response = table.scan()
            items = response.get('Items', [])
            return {'statusCode': 200, 'body': json.dumps(items)}
            
        item_id = event['pathParameters']['id']
        response = table.get_item(Key={'id': item_id})
        item = response.get('Item')
        return {
            'statusCode': 200 if item else 404,
            'body': json.dumps(item) if item else json.dumps({'error': 'Not found'})
        }
        
    elif http_method == 'POST':
        body = json.loads(event['body'])
        table.put_item(Item=body)
        return {'statusCode': 201, 'body': json.dumps(body)}
        
    elif http_method == 'DELETE':
        item_id = event['pathParameters']['id']
        table.delete_item(Key={'id': item_id})
        return {'statusCode': 204}
        
    return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid request'})}
""".format(table_name)

        try:
            # Create ZIP file with handler code
            import tempfile
            import zipfile
            
            zip_path = '/tmp/function.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('handler.py', handler_code)
            
            with open(zip_path, 'rb') as zip_file:
                zip_bytes = zip_file.read()
            
            # Create function
            response = self.lambda_client.create_function(
                FunctionName=function_name,
                Runtime=self.config.runtime,
                Role=role_arn,
                Handler='handler.handler',
                Code={'ZipFile': zip_bytes},
                MemorySize=self.config.memory_size,
                Timeout=self.config.timeout,
                Environment={
                    'Variables': {
                        'TABLE_NAME': table_name
                    }
                }
            )
            
            # Wait for function to be active
            self.lambda_client.get_waiter('function_active').wait(
                FunctionName=function_name
            )
            
            # Create version for provisioned concurrency
            version = self.lambda_client.publish_version(
                FunctionName=function_name
            )['Version']
            
            # Configure provisioned concurrency
            self.lambda_client.put_provisioned_concurrency_config(
                FunctionName=function_name,
                Qualifier=version,
                ProvisionedConcurrentExecutions=self.config.provisioned_concurrency
            )
            
            return response['FunctionArn']
            
        except self.lambda_client.exceptions.ResourceConflictException:
            return self.lambda_client.get_function(FunctionName=function_name)['Configuration']['FunctionArn']

    def create_api_gateway(self, function_arn: str) -> str:
        """Create API Gateway with routes for the Lambda function"""
        api_name = f"{self.config.service_name}-api"
        
        try:
            # Create REST API
            api = self.apigateway_client.create_rest_api(
                name=api_name,
                minimumCompressionSize=1024
            )
            
            # Get root resource ID
            resources = self.apigateway_client.get_resources(restApiId=api['id'])
            root_id = resources['items'][0]['id']
            
            # Create resource and methods
            resource = self.apigateway_client.create_resource(
                restApiId=api['id'],
                parentId=root_id,
                pathPart='items'
            )
            
            # Create methods
            methods = ['GET', 'POST', 'DELETE']
            for method in methods:
                self.apigateway_client.put_method(
                    restApiId=api['id'],
                    resourceId=resource['id'],
                    httpMethod=method,
                    authorizationType='NONE'
                )
                
                self.apigateway_client.put_integration(
                    restApiId=api['id'],
                    resourceId=resource['id'],
                    httpMethod=method,
                    type='AWS_PROXY',
                    integrationHttpMethod='POST',
                    uri=f'arn:aws:apigateway:{self.config.region}:lambda:path/2015-03-31/functions/{function_arn}/invocations'
                )
            
            # Deploy API
            self.apigateway_client.create_deployment(
                restApiId=api['id'],
                stageName='prod'
            )
            
            return api['id']
            
        except self.apigateway_client.exceptions.ConflictException:
            apis = self.apigateway_client.get_rest_apis()
            for api in apis['items']:
                if api['name'] == api_name:
                    return api['id']
            raise

# Example usage
if __name__ == '__main__':
    config = MicroserviceConfig(
        service_name='sample-service',
        region='us-east-1',
        github_token='YOUR_GITHUB_TOKEN',
        github_username='YOUR_GITHUB_USERNAME'
    )
    
    creator = AWSMicroserviceCreator(config)
    resources = creator.create_microservice()
    print("Created resources:", json.dumps(resources, indent=2))
