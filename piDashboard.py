import time
from datetime import datetime
import smbus
import spidev as SPI
import SSD1306

import os
import subprocess
import re
import psutil

import socket
import requests
import urllib.request
import netifaces as ni  # Install with: pip install netifaces

import RPi.GPIO as GPIO

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

import qrcode


#Configuring UPS:
class BusVoltageRange:
    """Constants for ``bus_voltage_range``"""
    RANGE_16V               = 0x00      # set bus voltage range to 16V
    RANGE_32V               = 0x01      # set bus voltage range to 32V (default)

class Gain:
    """Constants for ``gain``"""
    DIV_1_40MV              = 0x00      # shunt prog. gain set to  1, 40 mV range
    DIV_2_80MV              = 0x01      # shunt prog. gain set to /2, 80 mV range
    DIV_4_160MV             = 0x02      # shunt prog. gain set to /4, 160 mV range
    DIV_8_320MV             = 0x03      # shunt prog. gain set to /8, 320 mV range

class ADCResolution:
    """Constants for ``bus_adc_resolution`` or ``shunt_adc_resolution``"""
    ADCRES_9BIT_1S          = 0x00      #  9bit,   1 sample,     84us
    ADCRES_10BIT_1S         = 0x01      # 10bit,   1 sample,    148us
    ADCRES_11BIT_1S         = 0x02      # 11 bit,  1 sample,    276us
    ADCRES_12BIT_1S         = 0x03      # 12 bit,  1 sample,    532us
    ADCRES_12BIT_2S         = 0x09      # 12 bit,  2 samples,  1.06ms
    ADCRES_12BIT_4S         = 0x0A      # 12 bit,  4 samples,  2.13ms
    ADCRES_12BIT_8S         = 0x0B      # 12bit,   8 samples,  4.26ms
    ADCRES_12BIT_16S        = 0x0C      # 12bit,  16 samples,  8.51ms
    ADCRES_12BIT_32S        = 0x0D      # 12bit,  32 samples, 17.02ms
    ADCRES_12BIT_64S        = 0x0E      # 12bit,  64 samples, 34.05ms
    ADCRES_12BIT_128S       = 0x0F      # 12bit, 128 samples, 68.10ms

class Mode:
    """Constants for ``mode``"""
    POWERDOW                = 0x00      # power down
    SVOLT_TRIGGERED         = 0x01      # shunt voltage triggered
    BVOLT_TRIGGERED         = 0x02      # bus voltage triggered
    SANDBVOLT_TRIGGERED     = 0x03      # shunt and bus voltage triggered
    ADCOFF                  = 0x04      # ADC off
    SVOLT_CONTINUOUS        = 0x05      # shunt voltage continuous
    BVOLT_CONTINUOUS        = 0x06      # bus voltage continuous
    SANDBVOLT_CONTINUOUS    = 0x07      # shunt and bus voltage continuous


class INA219:
    def __init__(self, i2c_bus=1, addr=0x40):
        self.bus = smbus.SMBus(i2c_bus);
        self.addr = addr

        # Set chip to known config values to start
        self._cal_value = 0
        self._current_lsb = 0
        self._power_lsb = 0
        self.set_calibration_32V_2A()

    def read(self,address):
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return ((data[0] * 256 ) + data[1])

    def write(self,address,data):
        temp = [0,0]
        temp[1] = data & 0xFF
        temp[0] =(data & 0xFF00) >> 8
        self.bus.write_i2c_block_data(self.addr,address,temp)

    def set_calibration_32V_2A(self):
        """Configures to INA219 to be able to measure up to 32V and 2A of current. Counter
           overflow occurs at 3.2A.
           ..note :: These calculations assume a 0.1 shunt ohm resistor is present
        """
        # By default we use a pretty huge range for the input voltage,
        # which probably isn't the most appropriate choice for system
        # that don't use a lot of power.  But all of the calculations
        # are shown below if you want to change the settings.  You will
        # also need to change any relevant register settings, such as
        # setting the VBUS_MAX to 16V instead of 32V, etc.

        # VBUS_MAX = 32V             (Assumes 32V, can also be set to 16V)
        # VSHUNT_MAX = 0.32          (Assumes Gain 8, 320mV, can also be 0.16, 0.08, 0.04)
        # RSHUNT = 0.1               (Resistor value in ohms)

        # 1. Determine max possible current
        # MaxPossible_I = VSHUNT_MAX / RSHUNT
        # MaxPossible_I = 3.2A

        # 2. Determine max expected current
        # MaxExpected_I = 2.0A

        # 3. Calculate possible range of LSBs (Min = 15-bit, Max = 12-bit)
        # MinimumLSB = MaxExpected_I/32767
        # MinimumLSB = 0.000061              (61uA per bit)
        # MaximumLSB = MaxExpected_I/4096
        # MaximumLSB = 0,000488              (488uA per bit)

        # 4. Choose an LSB between the min and max values
        #    (Preferrably a roundish number close to MinLSB)
        # CurrentLSB = 0.0001 (100uA per bit)
        self._current_lsb = .1  # Current LSB = 100uA per bit

        # 5. Compute the calibration register
        # Cal = trunc (0.04096 / (Current_LSB * RSHUNT))
        # Cal = 4096 (0x1000)

        self._cal_value = 4096

        # 6. Calculate the power LSB
        # PowerLSB = 20 * CurrentLSB
        # PowerLSB = 0.002 (2mW per bit)
        self._power_lsb = .002  # Power LSB = 2mW per bit

        # 7. Compute the maximum current and shunt voltage values before overflow
        #
        # Max_Current = Current_LSB * 32767
        # Max_Current = 3.2767A before overflow
        #
        # If Max_Current > Max_Possible_I then
        #    Max_Current_Before_Overflow = MaxPossible_I
        # Else
        #    Max_Current_Before_Overflow = Max_Current
        # End If
        #
        # Max_ShuntVoltage = Max_Current_Before_Overflow * RSHUNT
        # Max_ShuntVoltage = 0.32V
        #
        # If Max_ShuntVoltage >= VSHUNT_MAX
        #    Max_ShuntVoltage_Before_Overflow = VSHUNT_MAX
        # Else
        #    Max_ShuntVoltage_Before_Overflow = Max_ShuntVoltage
        # End If

        # 8. Compute the Maximum Power
        # MaximumPower = Max_Current_Before_Overflow * VBUS_MAX
        # MaximumPower = 3.2 * 32V
        # MaximumPower = 102.4W

        # Set Calibration register to 'Cal' calculated above
        self.write(_REG_CALIBRATION,self._cal_value)

        # Set Config register to take into account the settings above
        self.bus_voltage_range = BusVoltageRange.RANGE_32V
        self.gain = Gain.DIV_8_320MV
        self.bus_adc_resolution = ADCResolution.ADCRES_12BIT_32S
        self.shunt_adc_resolution = ADCResolution.ADCRES_12BIT_32S
        self.mode = Mode.SANDBVOLT_CONTINUOUS
        self.config = self.bus_voltage_range << 13 | \
                      self.gain << 11 | \
                      self.bus_adc_resolution << 7 | \
                      self.shunt_adc_resolution << 3 | \
                      self.mode
        self.write(_REG_CONFIG,self.config)

    def getShuntVoltage_mV(self):
        self.write(_REG_CALIBRATION,self._cal_value)
        value = self.read(_REG_SHUNTVOLTAGE)
        if value > 32767:
            value -= 65535
        return value * 0.01

    def getBusVoltage_V(self):
        self.write(_REG_CALIBRATION,self._cal_value)
        self.read(_REG_BUSVOLTAGE)
        return (self.read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        return value * self._current_lsb

    def getPower_W(self):
        self.write(_REG_CALIBRATION,self._cal_value)
        value = self.read(_REG_POWER)
        if value > 32767:
            value -= 65535
        return value * self._power_lsb

# Config Register (R/W)
_REG_CONFIG = 0x00
# SHUNT VOLTAGE REGISTER (R)
_REG_SHUNTVOLTAGE = 0x01

# BUS VOLTAGE REGISTER (R)
_REG_BUSVOLTAGE = 0x02

# POWER REGISTER (R)
_REG_POWER = 0x03

# CURRENT REGISTER (R)
_REG_CURRENT = 0x04

# CALIBRATION REGISTER (R/W)
_REG_CALIBRATION = 0x05



#Configuring OLED display via SPI:
# Raspberry Pi pin configuration:
RST = 19
# Note the following are only used with SPI:
DC = 16
bus = 0
device = 0

# 128x64 display with hardware SPI:
disp = SSD1306.SSD1306(RST, DC, SPI.SpiDev(bus,device))
# Initialize library.
disp.begin()


#Configuring BMP280:




#Configuring DHT22:




#Configuring CO2 Meter:




while True:
  
  #Configuring CPU Readings:
  def get_cpu_temp():
    """
    Retrieves CPU temperature from Raspberry Pi using vcgencmd command.

    Returns:
        float: CPU temperature in degrees Celsius, or None if an error occurs.
    """
    try:
      # Execute the vcgencmd command to get temperature
      output = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, check=True)
      # Extract the temperature value from the output
      match = re.search(r"temp=(\d+\.?\d*)", output.stdout)
      if match:
        return float(match.group(1))
      else:
        return None
    except subprocess.CalledProcessError:
      # Error occurred during command execution
      return None

  # Get CPU temperature
  cpu_temp = get_cpu_temp()

  #if cpu_temp is not None:
    #print("CPU temperature: {:.1f}°C".format(cpu_temp))
  #else:
    #print("Error: Could not read CPU temperature")
    
  #Configuring Memory Readings:
  def get_ram_info():
    """
    Retrieves information about used RAM and total RAM capacity.

    Returns:
        tuple: (used_ram, total_ram), both in MB.
    """
    # Get memory usage statistics
    mem = psutil.virtual_memory()
    # Convert values from bytes to Megabytes (MB)
    used_ram = mem.used / (1024 * 1024)
    total_ram = mem.total / (1024 * 1024)
    #free_ram = mem.free / (1024 * 1024)
    return used_ram, total_ram

  # Get RAM information
  used_ram, total_ram = get_ram_info()

  # Print results. You can modify this for dashboard integration.
  #print(f"Used RAM: {used_ram:.1f} MB")
  #print(f"Total RAM: {total_ram:.1f} MB")
      
  
  
  
  
  #Getting Disk Space Info:
  def get_disk_usage(path):
    """
    Gets the free space and total capacity of the disk containing the specified path.

    Args:
        path: A path on the system disk.

    Returns:
        tuple: (free_space_gb, total_capacity_gb), both in GB, or None if an error occurs.
    """
    try:
      # Get disk usage statistics using os.statvfs
      stat = os.statvfs(path)
      # Calculate free space and total capacity in Gigabytes
      free_space_gb = stat.f_bavail * stat.f_frsize / (1024 * 1024 * 1024)
      total_capacity_gb = stat.f_blocks * stat.f_frsize / (1024 * 1024 * 1024)
      return free_space_gb, total_capacity_gb
    except OSError as e:
      print(f"Error getting disk usage: {e}")
      return None

  # Get disk usage information (assuming / is on the system disk)
  free_space_gb, total_capacity_gb = get_disk_usage("/")

  """
  if free_space_gb is not None and total_capacity_gb is not None:
    print(f"Free space: {free_space_gb:.2f} GB")
    print(f"Total capacity: {total_capacity_gb:.2f} GB")
  else:
    print("Error: Could not determine disk usage.")
  """  
  
  
  #Getting Network Stats:
  def get_network_info():
    """
    Gets information about the active network interface and its IP addresses.

    Returns:
        dict: A dictionary containing keys:
            - interface_name (str): Name of the active interface (e.g., wlan0, eth0).
            - internal_ip (str): Internal IP address on the active interface.
            - external_ip (str): External IP address obtained from a free service.
    """
    # Get list of available interfaces
    interfaces = ni.interfaces()
    # Find the active interface (connected and not loopback)
    active_interface = None
    for interface in interfaces:
      if ni.ifaddresses(interface)[ni.AF_INET] and not interface == 'lo':
        active_interface = interface
        break

    if not active_interface:
      return {'interface_name': None, 'internal_ip': None, 'external_ip': None}

    # Get internal IP address
    internal_ip = ni.ifaddresses(active_interface)[ni.AF_INET][0]['addr']

    # Get external IP address using a free service (example using ipify.org)
    external_ip = get_external_ip_from_service()

    return {
        'interface_name': active_interface,
        'internal_ip': internal_ip,
        'external_ip': external_ip
    }

  def get_external_ip_from_service():
    """
    Retrieves external IP address from a free service (example using ipify.org).

    Returns:
        str: External IP address, or None if an error occurs.
    """
    try:
      url = "https://api.ipify.org?format=text"  # Free JSON format available
      response = requests.get(url)
      return response.text.strip()
    except Exception as e:
      print(f"Error getting external IP: {e}")
      return None

  # Get network information
  network_info = get_network_info()
  
  """
  if network_info['interface_name']:
    print(f"Interface: {network_info['interface_name']}")
    print(f"Internal IP: {network_info['internal_ip']}")
    print(f"External IP: {network_info['external_ip']}")
  else:
    print("No active network interface found.")
  """
  
  
  #Configuring Motion Screen
  
  
  
  
  # Clear display.
  #disp.clear()
  #disp.display()

  # Create blank image for drawing.
  # Make sure to create image with mode '1' for 1-bit color.
  width = disp.width
  height = disp.height
  image = Image.new('1', (width, height))

  # Get drawing object to draw on image.
  draw = ImageDraw.Draw(image)

  # Draw a black filled box to clear the image.
  draw.rectangle((0,0,width,height), outline=0, fill=0)

  # Draw some shapes.
  # First define some constants to allow easy resizing of shapes.
  padding = 1
  top = padding
  x = padding
  
  # Load default font.
  font = ImageFont.load_default()
  headerFont = ImageFont.truetype('NimbusSanL-Bol.otf', 10.2)
  textFont = ImageFont.truetype('NimbusSanL-Reg.otf', 10)

  # Alternatively load a TTF font.  Make sure the .ttf font file is in the same directory as the python script!
  # Some other nice fonts to try: http://www.dafont.com/bitmap.php
  # Icons website: https://icons8.com/line-awesome
  #font = ImageFont.truetype('PixelOperator.ttf', 16)
  #icon_font= ImageFont.truetype('lineawesome-webfont.ttf', 18)
  
  # Clear display.
  disp.clear()
   
  # Create an INA219 instance.
  ina219 = INA219(addr=0x42)
  bus_voltage = ina219.getBusVoltage_V()             # voltage on V- (load side)
  shunt_voltage = ina219.getShuntVoltage_mV() / 1000 # voltage between V+ and V- across the shunt
  current = ina219.getCurrent_mA()                   # current in mA
  power = ina219.getPower_W()                        # power in W
  p = (bus_voltage - 6)/2.4*100
  if(p > 100):p = 100
  if(p < 0):p = 0
  
  # Get current date and time
  now = datetime.now()

  # Format the date and time string
  #formatted_datetime = now.strftime("%a|%b %d %Y|%H:%M")
  formatted_datetime = now.strftime("%a | %b %d %Y | %H:%M")
  
  # Draw data on the image
  draw.text((x, top), (formatted_datetime), font=headerFont, fill=255)
  #draw.text((x, top), ("_______________________"), font=headerFont, fill=255)
  draw.text((x, top+15), " CPU Temp: " + str(cpu_temp) + " ºC", font=textFont, fill=255)
  draw.text((x, top+25), f" RAM: {used_ram:.0f} of {total_ram:.0f} MB Used", font=textFont, fill=255)
  draw.text((x, top+35), f" Disk: {free_space_gb:.0f} of {total_capacity_gb:.0f} GB Free", font=textFont, fill=255)
  #draw.text((x, top+45), "# Power: {:1.3f} W".format(power), font=textFont, fill=255)
  if current < 0:
    draw.text((x, top+45), " Power: {:1.3f} W".format(power), font=textFont, fill=255)
  elif current >0:
    draw.text((x, top+45), " Charging at {:1.4f} A".format(current/1000), font=textFont, fill=255)
  draw.text((x, top+55), " Battery: {:1.1f}%".format(p), font=textFont, fill=255)
  # Display image.
  disp.image(image)
  disp.display()
  
  #additional clear display image:
  #blankImage = Image.new('1', (128, 64))  # Create a new black image
  #cleaning the display
  #draw = ImageDraw.Draw(blankImage)
  
  #checking for joystick buttons being pressed:
  
  #center button to display QR code:
  username = os.getlogin()
  KEY = 20
  GPIO.setmode(GPIO.BCM)
  GPIO.setup(KEY,GPIO.IN,GPIO.PUD_UP)
  
  if GPIO.input(KEY) == 0:
    while GPIO.input(KEY) == 0:
      time.sleep(0.01)
      print("Center - Showing QR Code to Connect via WebSSH:")

      draw.rectangle((0,0,width,height), outline=0, fill=0)      

      url = "ssh://" + str(username) + "@" + "192.168.81.99"  # Replace with your desired URL
      print (url)

      # Create the QR code
      qr = qrcode.QRCode(
          version=1,  # Adjust version for longer URLs if needed
          box_size=1,
          border=0  # Remove border for cleaner appearance
      )
      qr.add_data(url)
      qr.make(fit=True)

      # Generate a white QR code image
      qr_img = qr.make_image(fill_color="white", back_color="black")

      # Resize to 64x64 pixels
      qr_img = qr_img.resize((64, 64), Image.LANCZOS)

      # Create a blank image for the display
      oled_img = Image.new("1", (128, 64), color=0)  # 1-bit mode for OLED compatibility

      # Calculate center coordinates
      x_center = (oled_img.width - qr_img.width) // 2
      y_center = (oled_img.height - qr_img.height) // 2

      # Paste QR code onto the display image
      oled_img.paste(qr_img, (x_center, y_center))

      disp.image(oled_img)
      disp.display()
      time.sleep(6)

              
  
  #now checking for arrow keys:
  address = 0x20
  bus = smbus.SMBus(1)
  bus.write_byte(address,0x0F|bus.read_byte(address))
  value = bus.read_byte(address) | 0xF0
  
  while value != 0xFF:
    if (value | 0xFE) != 0xFF:
      print("left - Network")
      #Interface, Int IP, Ext IP
      draw.rectangle((0,0,width,height), outline=0, fill=0)
      draw.text((x, top), ("Network Stats:"), font=headerFont, fill=255)
      #draw.text((x, top+1), "________", font=textFont, fill=255)
      if network_info['interface_name']:
        draw.text((x, top+15), f" Interface: {network_info['interface_name']}", font=textFont, fill=255)
        draw.text((x, top+27), f" Int IP: {network_info['internal_ip']}", font=textFont, fill=255)
        draw.text((x, top+39), f" User: {username}", font=textFont, fill=255)
        draw.text((x, top+51), f" Ext IP: {network_info['external_ip']}", font=textFont, fill=255)
      else:
        draw.text((x, top+27), "No active ", font=textFont, fill=255)
        draw.text((x, top+37), "network interface", font=textFont, fill=255)
        draw.text((x, top+47), "found", font=textFont, fill=255)
      disp.image(image)
      disp.display()
      time.sleep(6)
      bus.write_byte(address,0x0F|bus.read_byte(address))
      value = bus.read_byte(address) | 0xF0
      time.sleep(0.1)
      
    elif (value | 0xFD) != 0xFF:
      print("up - UPS Stats")
      draw.rectangle((0,0,width,height), outline=0, fill=0)
      draw.text((x, top), ("UPS Stats:"), font=headerFont, fill=255)
      #draw.text((x, top+1), "__________", font=textFont, fill=255)
      draw.text((x, top+15), " Load Voltage: {:1.2f} V".format(bus_voltage), font=textFont, fill=255)
      draw.text((x, top+27), " Current: {:1.4f} A".format(current/1000), font=textFont, fill=255)
      draw.text((x, top+39), " Power: {:1.3f} W".format(power), font=textFont, fill=255)
      draw.text((x, top+51), " Percent: {:1.1f}%".format(p), font=textFont, fill=255)
      disp.image(image)
      disp.display()
      time.sleep(6)
      bus.write_byte(address,0x0F|bus.read_byte(address))
      value = bus.read_byte(address) | 0xF0
      time.sleep(0.1)
      
    elif (value | 0xFB) != 0xFF:
      print("down - Room Conditions:")
      draw.rectangle((0,0,width,height), outline=0, fill=0)
      draw.text((x, top), ("Room Conditions:"), font=headerFont, fill=255)
      #draw.text((x, top+1), "________", font=textFont, fill=255)
      draw.text((x, top+15), " Temp: 24 ºС", font=textFont, fill=255)
      draw.text((x, top+27), " Hum: 36%", font=textFont, fill=255)
      draw.text((x, top+39), " Pressure: 1 hPa", font=textFont, fill=255)
      draw.text((x, top+51), " CO2: 37%", font=textFont, fill=255)
      disp.image(image)
      disp.display()
      time.sleep(6)
      bus.write_byte(address,0x0F|bus.read_byte(address))
      value = bus.read_byte(address) | 0xF0
      time.sleep(0.1)
      
    elif (value | 0xFF) == 0xFF:
      print("right - Motion")
      draw.rectangle((0,0,width,height), outline=0, fill=0)      
      draw.text((x, top), ("Surveillance:"), font=headerFont, fill=255)
      #draw.text((x, top+1), "________", font=textFont, fill=255)
      draw.text((x, top+15), " Status: ON", font=textFont, fill=255)
      draw.text((x, top+30), " Storage: SD/USB", font=textFont, fill=255)
      draw.text((x, top+45), f" Disk: {free_space_gb:.0f} of {total_capacity_gb:.0f} GB Free", font=textFont, fill=255)
      #draw.text((x, top+45), "***: ", font=textFont, fill=255)
      disp.image(image)
      disp.display()
      time.sleep(6)
      bus.write_byte(address,0x0F|bus.read_byte(address))
      value = bus.read_byte(address) | 0xF0
      time.sleep(0.1)
      
      
   
  #checking for battery level to initiate safe shutdown below 10%:
  if p < 10 and current < 0:
    beepAddress = 0x20
    def beep_on():
      bus.write_byte(address,0x7F&bus.read_byte(beepAddress))
    def beep_off():
      bus.write_byte(address,0x80|bus.read_byte(beepAddress))
    def led_off():
      bus.write_byte(address,0x10|bus.read_byte(address))
    def led_on():
      bus.write_byte(address,0xEF&bus.read_byte(address))
                     
    draw.rectangle((0,0,width,height), outline=0, fill=0)  
    draw.text((x, top), ("WARNING:"), font=headerFont, fill=255)
    draw.text((x, top+15), ("Low Battery"), font=headerFont, fill=255)
    draw.text((x, top+30), ("Shutting down"), font=headerFont, fill=255)
    disp.image(image)
    disp.display()
    for i in range(1, 4):
      beep_on()
      led_on()
      time.sleep(0.1)
      beep_off()
      led_off()
      time.sleep(0.1)
    time.sleep(6)
    exit_status = os.system("sudo poweroff")
    #elif value != 0xFF:
    #  if (value | 0xFE) != 0xFF:
    #    print("left")
    #    disp.clear()
    #    draw = ImageDraw.Draw(image)
    #    draw.text((x, top), "LEFT", font=font, fill=255)
    #    time.sleep(3)
    #  elif (value | 0xFD) != 0xFF:
    #    print("up")
    #  elif (value | 0xFB) != 0xFF:
    #    print("down")
    #  else :
    #    print("right")
    # bus.write_byte(address,0x0F|bus.read_byte(address))
    # value = bus.read_byte(address) | 0xF0
    #draw = ImageDraw.Draw(image)
                
                
