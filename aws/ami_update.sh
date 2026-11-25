#!/usr/bin/env bash
source $(dirname "${BASH_SOURCE[0]}")/../source

arch=${ARCH:-$(arch)}

# Trap function to terminate our running instance when the script exits.
function on_exit {
    [ "${NO_TERMINATE:-0}" -eq 0 ] && aws_terminate_instance $state_dir
}

if ! aws sts get-caller-identity > /dev/null 2>&1; then
  echo "AWS CLI credentials for the CI account are required (aws sts get-caller-identity failed)."
  exit 1
fi

case "$arch" in
  "amd64")
    ami="ami-04f167a56786e4b09"
    ;;
  "arm64")
    ami="ami-0ae6f07ad3a8ef182"
    ;;
  *)
    echo "Unknown arch: $ARCH"
    exit 1
esac

# Request new instance (ami: ubuntu 24.04 LTS). Like a build instance, it has no key pair and is driven over SSM.
state_dir=$(mktemp -d /tmp/aws_request_instance.XXXXXX)
trap on_exit EXIT
# Like a build instance's, its lease is renewed while this script runs (see internal/source_instance_lease).
# create-image below reboots the instance to image it, so shutdown stays as stop rather than terminate.
aws_instance_lease $state_dir $(($(date +%s) + ${AWS_SHUTDOWN_TIME:-60} * 60)) $$ &
AMI=$ami AWS_SHUTDOWN_TERMINATES=0 aws_request_instance ami_update_$arch 4 $arch $state_dir
iid=$(cat $state_dir/iid | tr -d '\n\r' | xargs)

echo "Instance id: $iid"

# Initial setup.
ssm_send_command "$iid" '
  set -e
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
  apt update
  apt install -y apt-transport-https ca-certificates curl software-properties-common docker-ce
  usermod -aG docker ubuntu
  sudo -u ubuntu mkdir -p /home/ubuntu/.aws
'

# Download uncompressed crs onto machine for fast cold starts (deletes compressed if present).
SSM_RUN_AS=ubuntu ssm_send_command "$iid" "export HOME=/home/ubuntu CRS_FORMAT=uncompressed; cd ~; $(cat download_crs.sh)"

# Pull devbox onto host, and build into docker-in-docker volume.
ssm_send_command "$iid" "
  docker run --privileged --rm -v bootstrap_ci_local_docker:/var/lib/docker aztecprotocol/devbox:3.2 bash -c \"
    docker pull aztecprotocol/build:3.2
  \"
"

# No image carries an SSH key: instances made from it take none, and are reached only through SSM.
ssm_send_command "$iid" 'rm -f /home/ubuntu/.ssh/authorized_keys /root/.ssh/authorized_keys'

if [ "${NO_AMI:-0}" -eq 0 ]; then
  export AWS_DEFAULT_REGION=us-east-2
  ami_id=$(aws ec2 create-image \
    --instance-id "$iid" \
    --name "build-instance-$arch-$(date +'%d%m%y%H%M')" \
    --query "ImageId" \
    --output text)
  echo "Waiting for AMI to be created: $ami_id"
  while ! aws ec2 wait image-available --image-ids "$ami_id"; do true; done
  echo "$ami_id" > ami_id_$arch
  echo "Done."
fi
