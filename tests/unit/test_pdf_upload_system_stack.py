import aws_cdk as core
import aws_cdk.assertions as assertions

from pdf_upload_system.pdf_upload_system_stack import PdfUploadSystemStack

# example tests. To run these tests, uncomment this file along with the example
# resource in pdf_upload_system/pdf_upload_system_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = PdfUploadSystemStack(app, "pdf-upload-system")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
