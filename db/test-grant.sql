-- Allow Django's test runner to create and drop its isolated database.
GRANT ALL ON `test\_health`.* TO 'health'@'%';
