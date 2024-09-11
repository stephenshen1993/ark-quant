package com.stephenshen.arkquant.entity;

import com.alibaba.fastjson.annotation.JSONField;
import lombok.Data;

/**
 * 交易计划
 *
 * @author stephenshen
 * @date 2024/8/28 07:04:49
 */
@Data
public class TradingPlan {
    // 标的编码
    @JSONField(ordinal = 1)
    private String symbol;
    // 标的名称
    @JSONField(ordinal = 2)
    private String name;
    // 标的价格
    @JSONField(ordinal = 3)
    private Double price;
    // 交易数量
    @JSONField(ordinal = 4)
    private Integer quantity;
    // 交易价值
    @JSONField(ordinal = 5)
    private Double value;

    public TradingPlan(Security security, Integer quantity) {
        this.symbol = security.getSymbol();
        this.name = security.getName();
        this.price = security.getPrice();
        this.quantity = quantity;
        // 交易价值
        this.value = this.price * this.quantity;
    }
}
