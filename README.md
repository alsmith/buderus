Monitoring system for Buderus KM200 (and likely other control modules) boilers
==============================================================================

Installation steps:

1) You will need to find the Buderus salt, http://lmgtfy.com/?q=Buderus+KM200+salt
ought to find it... create the array of salt bytes into buderus.py as follows:

salt = [ 0xAA, 0xBB, 0xCC, 0xDD ]

Expect a total of 32 bytes worth of salt data.

2) Create a personal password using the EasyControl phone app.

3) Update config.ini with your personal password, your gateway's
password (without the dashes) and your gateway's hostname.

4) Create the new database.

mysql -u abcdef -p "CREATE DATABASE buderus"

5) Create the required database tables. In this example we have created
a 'buderus' database that we will create our tables in:

mysql -u abcdef -p buderus < createDatabase.sql

6) Update config.ini with your database credentials, hostname and database name.

7) Run ./restart.sh

8) Go to http://hostname:8001 and enjoy.

-- 
Al Smith <ajs@aeschi.eu>

