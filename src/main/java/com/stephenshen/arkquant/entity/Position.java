package com.stephenshen.arkquant.entity;

import lombok.Data;

/**
 * 持仓
 *
 * @author stephenshen
 * @date 2024/9/1 10:52:42
 */
@Data
public class Position {
    private String symbol;
    private String name;
    private Double price;
    private Integer quantity;

    public Position(String symbol, String name, Double price, Integer quantity) {
        this.symbol = symbol;
        this.name = name;
        this.price = price != null ? price : 0;
        this.quantity = quantity;
    }

    public double getValue() {
        return this.price * this.quantity;
    }
}
