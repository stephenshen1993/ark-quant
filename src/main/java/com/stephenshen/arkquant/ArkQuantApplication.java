package com.stephenshen.arkquant;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.openfeign.EnableFeignClients;

@EnableFeignClients
@SpringBootApplication
public class ArkQuantApplication {

    public static void main(String[] args) {
        SpringApplication.run(ArkQuantApplication.class, args);
    }
}
