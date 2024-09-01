package com.stephenshen.arkquant.entity;

import lombok.Getter;

import java.util.HashMap;
import java.util.Map;

/**
 * 交易计划
 *
 * @author stephenshen
 * @date 2024/8/28 07:04:49
 */
@Getter
public class TradingPlan {
    private final Map<String, Integer> buyPlan = new HashMap<>();
    private final Map<String, Integer> sellPlan = new HashMap<>();

    public void addBuy(String symbol, int quantity) {
        buyPlan.put(symbol, quantity);
    }

    public void addSell(String symbol, int quantity) {
        sellPlan.put(symbol, quantity);
    }
}
