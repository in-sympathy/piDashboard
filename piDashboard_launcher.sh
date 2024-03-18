#!/bin/bash

echo "Fixing gpiomem access: "
sleep 1
sudo groupadd gpio
sudo usermod -a -G gpio $USER
sudo grep gpio /etc/group
sudo chown root.gpio /dev/gpiomem
sudo chmod g+rw /dev/gpiomem

echo "Done. Switching to a script installation directory: "
script_dir=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
cd "${script_dir}"
echo "piDashboard is located in ${script_dir}"
sleep 1

#If any venv is active:
if [[ "${VIRTUAL_ENV}" != "" ]]; then
  echo "Deactivating virtual environment..."
  deactivate
else
  echo "No virtual environment is currently active."
fi



#getting a backup of config.txt for comparison:
file1="/boot/firmware/config.txt"
file2="config.bak"
sudo cp "$file1" "$file2"

echo "Activating necessary interfaces:"
sleep 1
# Check camera status using non-interactive raspi-config
camera_enabled=$(sudo raspi-config nonint get_camera)

# Check if exit code is 0 (camera enabled)
if [ "$camera_enabled" -eq 0 ]; then
  echo "Camera already enabled."
  configChanged=0
else
  # Enable camera using non-interactive raspi-config (requires sudo)
  sudo raspi-config nonint do_camera 0
  echo "Camera enabled successfully."
  configChanged=1
fi

# Check SPI status (no sudo needed)
spi_enabled=$(sudo raspi-config nonint get_spi)

# Enable SPI if currently disabled (uses sudo)
if [ "$spi_enabled" -eq 1 ]; then
  echo "SPI is currently disabled. Enabling..."
  sudo raspi-config nonint do_spi 0
  echo "SPI enabled."
  configChanged=1
else
  echo "SPI is already enabled."
  configChanged=0
fi


# Check I2C status (no sudo needed)
i2c_status=$(sudo raspi-config nonint get_i2c)

# Enable I2C if currently disabled (uses sudo)
if [[ $i2c_status -eq 1 ]]; then
  echo "I2C is currently disabled. Enabling..."
  sudo raspi-config nonint do_i2c 0
  configChanged=1
else
  echo "I2C is already enabled."
  configChanged=0
fi


# Check serial hardware status (using sudo internally)
serial_hw_status=$(check_and_run_sudo raspi-config nonint get_serial_hw)

# Enable serial if status is 1 (using sudo internally)
if [[ $serial_hw_status -eq 1 ]]; then
  check_and_run_sudo raspi-config nonint do_serial 0
  configChanged=1
  echo "Serial hardware enabled."
else
  configChanged=0
  echo "Serial hardware already enabled or not applicable. If needed - please check manually over sudo raspi-config and go to Interfaces"
fi


# Check onewire status (using sudo)
onewire_enabled=$(sudo raspi-config nonint get_onewire)

# Enable onewire if disabled
if [[ $onewire_enabled -eq 1 ]]; then
  echo "OneWire is currently disabled. Enabling..."
  sudo raspi-config nonint do_onewire 0
  configChanged=1
elif [[ $onewire_enabled -eq 0 ]]; then
  echo "OneWire is already enabled"
  configChanged=0
fi



echo "Creating cron job: "
# Define the cron job entry
CRON_ENTRY="@reboot ${script_dir}/piDashboard_launcher.sh"

# Function to check and add cron job (using sudo)
add_cron_job() {
  # Create temporary file with existing cron entries (no sudo needed)
  temp_file="$PWD/crontab.tmp"
  crontab -l 2>/dev/null > "$temp_file" || touch "$temp_file"  # Suppress errors, create empty file if no crontab

  # Check if job is present (avoiding grep on potentially empty file)
  if ! grep -q "$CRON_ENTRY" "$temp_file"; then
    # Add cron job entry to the end of the temporary file
    echo "$CRON_ENTRY" >> "$temp_file"
  fi

  # Update crontab with temporary file content (uses sudo)
  crontab "$temp_file"
  if [ $? -eq 0 ]; then
    echo "Cron job added/updated (if necessary): '$CRON_ENTRY'"
  fi

  # Clean up temporary file (no sudo needed)
  rm -f "$temp_file"
}

# Check for cron job and config change (combined condition)
if [[ ! $(crontab -l 2>/dev/null) =~ $CRON_ENTRY ]] || [[ $configChanged -eq 1 ]]; then
  # Call function to add/update cron job (uses sudo internally)
  add_cron_job

  # Prompt for reboot
  read -r -p "Cron job or configuration changed. Reboot now? (y/N) " response
  case "$response" in
    [yY]*)
      sudo reboot
      ;;
  esac  
fi

# Include a placeholder for the configChanged variable (modify as needed)
configChanged=0  # Replace with logic to determine configuration change

# Optional: Inform about existing cron job (if script reaches this point)
if [[ $(crontab -l 2>/dev/null) =~ $CRON_ENTRY ]]; then
  echo "Cron job already present: '$CRON_ENTRY'"
fi




#Fixing weird "externally managed environment error in Python 3.11:
if [[ -f "/usr/lib/python3.11/EXTERNALLY-MANAGED" ]]; then
  sudo mv /usr/lib/python3.11/EXTERNALLY-MANAGED /usr/lib/python3.11/EXTERNALLY-MANAGED.old
fi

if [[ -f "piDashboard/bin/activate" ]]; then
  echo "Found venv. Trying to activate"
  source ${PWD##*/}/bin/activate
  sleep 1
  echo "Activated. Upgrading requirements"
  #pip install --upgrade -r requirements.txt
  echo "Installed all of the requirements"
else
  echo "Creating virtual environment:"
  sleep 1
  python -m venv ${PWD##*/}
  echo "Activating venv:"
  sleep 1
  source ${PWD##*/}/bin/activate
  pip3 install -r requirements.txt
  pip freeze > requirements.txt
  echo "Ready. Launching piDashboard:"
  sleep 1
fi
python3 piDashboard.py