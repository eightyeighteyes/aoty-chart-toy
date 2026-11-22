# Account-wide service-linked roles that the env stacks need but their deploy roles may not
# create (they can only create roles under /kamp-aoty/<env>/).
#
# ECS (AOTY-20): attaching FARGATE_SPOT to the index cluster needs AWSServiceRoleForECS. ECS
# makes it on first use only if the caller can iam:CreateServiceLinkedRole, so the first
# sandbox deploy failed with "Unable to assume the service linked role". It's AWS-managed
# and shared by every ECS cluster in the account.
resource "aws_iam_service_linked_role" "ecs" {
  aws_service_name = "ecs.amazonaws.com"
  description      = "ECS service-linked role (kamp-aoty index build, AOTY-20)"
}
