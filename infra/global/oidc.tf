# One provider per URL per account. kamp-site can reuse it later through a data source
# (aws_iam_openid_connect_provider by url) when it moves off IAM user keys.
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
