package com.stephenshen.arkquant.strategy;

import com.stephenshen.arkquant.entity.TradingPlan;

/**
 * 交易策略
 *
 * @author stephenshen
 * @date 2024/9/1 18:32:29
 */
public interface TradingStrategy<T extends TradingStrategy.Context> {

    /**
     * 制定交易计划
     * @param context
     * @return
     */
    TradingPlan makeTradingPlan(T context);

    /**
     * 策略上下文
     */
    interface Context {}
}
