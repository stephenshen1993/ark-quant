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
    private Double value;

    public Position(String symbol, String name, Double price, Integer quantity) {
        this.symbol = symbol;
        this.name = name;
        this.price = price;
        this.quantity = quantity;
        this.value = price * quantity;
    }

    /**
     * 更新持仓数量
     * @param quantity
     */
    public void updateQuantity(Integer quantity) {
        this.quantity = quantity;
        this.value = price * quantity;
    }
}
