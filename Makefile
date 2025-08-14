.PHONY: build run stop logs

build:
	docker build -t parser:$(VERSION) .

run:
	docker run -d \
        --name parser \
        -p 1338:1338 \
        --restart unless-stopped \
        parser:$(VERSION)

stop:
    docker stop parser

logs:
	docker logs -f parser