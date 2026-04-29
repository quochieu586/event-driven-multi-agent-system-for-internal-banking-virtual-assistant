import boto3
import json
import os
from decimal import Decimal

# 1. This helper class solves the "Not JSON serializable" error
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            # Check if it's a whole number or has decimals
            if obj % 1 == 0:
                return int(obj)
            else:
                return float(obj)
        return super(DecimalEncoder, self).default(obj)

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get("TABLE_NAME") # Or your hardcoded table name

def handler(event, context):
    table = dynamodb.Table(TABLE_NAME)
    
    # 1. Extract request_id from query string parameters
    # Expected URL format: .../dev/status?request_id=your-uuid-here
    query_params = event.get('queryStringParameters') or {}
    request_id = query_params.get('request_id')

    if not request_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing request_id parameter'})
        }

    try:
        # 2. Attempt to retrieve the item
        response = table.get_item(Key={'id': request_id})
        item = response.get('Item')

        # 3. Handle the "Not Ready" vs "Ready" logic
        if not item or item.get('status') == 'PROCESSING':
            # Item doesn't exist yet (Specialist hasn't finished or Supervisor hasn't written)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'PROCESSING',
                    'message': 'Your request is still being processed.'
                })
            }

        returned_dict = json.dumps(item, cls=DecimalEncoder)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'  # Important for Streamlit/Frontend CORS
            },
            'body': json.dumps(item, cls=DecimalEncoder)
        }

    except Exception as e:
        print(f"Error accessing DynamoDB: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error accessing database'})
        }